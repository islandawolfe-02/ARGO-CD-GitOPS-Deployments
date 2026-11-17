import io
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guard import check_resources, read_resource, resource_path
from ownership import OWNER_LABEL, REPOSITORY, check_existing
from parent import GUARD_IMAGE, GUARD_NAME, PARENT_NAME, install, parent_manifest
import test_deployments


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.manifest = parent_manifest(self.root)

    def test_parent_watches_only_bootstrap_without_pruning_or_finalizers(self):
        app = self.manifest['items'][-1]
        self.assertEqual(app['spec']['source']['repoURL'], REPOSITORY)
        self.assertEqual(app['spec']['source']['path'], 'bootstrap')
        self.assertEqual(app['spec']['source']['directory']['include'], 'cd-*.json')
        self.assertEqual(app['spec']['syncPolicy']['automated'], {'prune': False, 'selfHeal': True})
        self.assertNotIn('finalizers', app['metadata'])
        project = self.manifest['items'][-2]['spec']
        self.assertEqual(project['destinations'][0]['namespace'], 'argocd')
        self.assertEqual(project['clusterResourceWhitelist'], [{'group': '', 'kind': 'Namespace'}])
        self.assertEqual({item['kind'] for item in project['namespaceResourceWhitelist']}, {'Application', 'AppProject', 'Job'})

    def test_guard_account_has_get_only_and_no_secret_or_write_access(self):
        role = next(item for item in self.manifest['items'] if item['kind'] == 'ClusterRole')
        for rule in role['rules']:
            self.assertEqual(rule['verbs'], ['get'])
            self.assertNotIn('*', rule['resources'])
            self.assertNotIn('secrets', rule['resources'])
        binding = next(item for item in self.manifest['items'] if item['kind'] == 'ClusterRoleBinding')
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': GUARD_NAME, 'namespace': 'argocd'}])

    def test_guard_config_uses_the_current_shared_ownership_code(self):
        config = next(item for item in self.manifest['items'] if item['kind'] == 'ConfigMap')
        self.assertEqual(config['data']['ownership.py'], (self.root / 'scripts/ownership.py').read_text())
        self.assertEqual(config['data']['guard.py'], (self.root / 'scripts/guard.py').read_text())
        self.assertRegex(GUARD_IMAGE, r'@sha256:[0-9a-f]{64}$')

    def test_parent_installer_refuses_unowned_existing_objects_before_apply(self):
        unowned = subprocess.CompletedProcess([], 0, stdout='{"metadata":{"labels":{}}}')
        with patch('bootstrap.subprocess.run', return_value=unowned) as run:
            with self.assertRaisesRegex(ValueError, 'take over'):
                install(self.root)
            self.assertEqual(run.call_count, 1)

    def test_parent_installer_preflights_every_seed_object_before_apply(self):
        absent = subprocess.CompletedProcess([], 0, stdout='')
        with patch('bootstrap.subprocess.run', return_value=absent) as run:
            install(self.root)
            self.assertEqual(run.call_count, len(self.manifest['items']) + 1)
            self.assertEqual(run.call_args.args[0], ['kubectl', '--context', 'minikube', 'apply', '-f', '-'])
            applied = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(applied['items'][-1]['metadata']['name'], PARENT_NAME)

    def test_preview_cli_never_invokes_kubectl(self):
        with patch('sys.argv', ['parent.py']), patch('bootstrap.subprocess.run') as run, redirect_stdout(io.StringIO()) as output:
            runpy.run_path(str(self.root / 'scripts/parent.py'), run_name='__main__')
            self.assertEqual(json.loads(output.getvalue())['items'][-1]['metadata']['name'], PARENT_NAME)
            run.assert_not_called()

    def test_checked_in_records_match_the_current_renderer(self):
        from render import render
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary)
            shutil.copytree(self.root / 'projects', generated / 'projects')
            for record_path in (self.root / 'deployments').glob('*/*/*/record.json'):
                record = json.loads(record_path.read_text())
                identity, directory = render(generated, {key: value for key, value in record.items() if key != 'deployment_id'})
                for filename in ('resources.json', 'record.json', 'commands.txt'):
                    self.assertEqual((directory / filename).read_text(), (record_path.parent / filename).read_text())
                self.assertEqual((generated / f'bootstrap/{identity}.json').read_text(),
                                 (self.root / f'bootstrap/{identity}.json').read_text())


class HookTests(unittest.TestCase):
    def setUp(self):
        test_deployments.DeploymentTests.setUp(self)

    def test_guard_runs_before_namespace_project_and_child_application(self):
        from render import render
        identity, directory = render(self.root, self.request)
        items = json.loads((self.root / f'bootstrap/{identity}.json').read_text())['items']
        self.assertEqual([item['metadata']['annotations']['argocd.argoproj.io/sync-wave'] for item in items[:3]], ['-2', '-1', '0'])
        hook = items[3]
        self.assertEqual(hook['metadata']['annotations']['argocd.argoproj.io/hook'], 'PreSync')
        self.assertEqual(hook['metadata']['annotations']['argocd.argoproj.io/hook-delete-policy'], 'HookSucceeded')
        self.assertNotIn('name', hook['metadata'])
        self.assertTrue(hook['metadata']['generateName'].startswith(identity + '-'))
        pod = hook['spec']['template']['spec']
        self.assertEqual(pod['serviceAccountName'], GUARD_NAME)
        self.assertEqual(pod['containers'][0]['image'], GUARD_IMAGE)
        inputs = json.loads(next(item['value'] for item in pod['containers'][0]['env'] if item['name'] == 'EXPECTED_RESOURCES'))
        self.assertEqual(len(inputs), 11)
        self.assertEqual({item['metadata']['namespace'] for item in inputs if item['kind'] == 'Deployment'}, {'my-application'})
        commands = (directory / 'commands.txt').read_text()
        self.assertNotIn('scripts/bootstrap.py', commands)
        self.assertIn('cd-bootstrap ' + identity, commands)

    def test_manual_fallback_does_not_apply_the_argo_hook(self):
        from bootstrap import bootstrap
        from render import render
        identity, _ = render(self.root, self.request)
        with patch('bootstrap.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout='')) as run:
            bootstrap(self.root, identity)
            applied = json.loads(run.call_args.kwargs['input'])
            self.assertEqual([item['kind'] for item in applied['items']], ['Namespace', 'AppProject', 'Application'])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.expected = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'demo-app', 'labels': {OWNER_LABEL: 'cd-example'}}}

    def test_missing_or_already_owned_resources_are_allowed(self):
        check_resources([self.expected], lambda item: None)
        check_resources([self.expected], lambda item: self.expected)

    def test_unowned_resources_stop_the_guard(self):
        with self.assertRaisesRegex(ValueError, 'take over'):
            check_resources([self.expected], lambda item: {'metadata': {'labels': {}}})

    def test_existing_application_must_keep_its_path_project_and_destination(self):
        expected = {'kind': 'Application', 'metadata': self.expected['metadata'], 'spec': {
            'project': 'cd-example', 'source': {'repoURL': REPOSITORY, 'path': 'deployments/one'},
            'destination': {'namespace': 'demo-app', 'server': 'https://kubernetes.default.svc'}}}
        for field in ('path', 'repoURL'):
            existing = json.loads(json.dumps(expected))
            existing['spec']['source'][field] = 'different'
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_existing(expected, existing)

    def test_api_routes_namespaced_and_cluster_objects(self):
        self.assertEqual(resource_path(self.expected), '/api/v1/namespaces/demo-app')
        app = {'kind': 'Deployment', 'metadata': {'name': 'app', 'namespace': 'demo-app'}}
        self.assertEqual(resource_path(app), '/apis/apps/v1/namespaces/demo-app/deployments/app')

    def test_api_not_found_is_allowed_but_permission_and_server_errors_fail(self):
        for status in (404, 401, 403, 500):
            error = HTTPError('https://kubernetes.default.svc', status, 'test', {}, None)
            with self.subTest(status=status), patch('guard.urlopen', side_effect=error):
                if status == 404:
                    self.assertIsNone(read_resource(self.expected, 'test-token', None))
                else:
                    with self.assertRaisesRegex(RuntimeError, str(status)):
                        read_resource(self.expected, 'test-token', None)

    def test_api_read_uses_tls_and_a_get_request(self):
        with patch('guard.urlopen') as open_url:
            open_url.return_value.__enter__.return_value = io.StringIO(json.dumps(self.expected))
            context = object()
            self.assertEqual(read_resource(self.expected, 'test-token', context), self.expected)
            self.assertEqual(open_url.call_args.args[0].get_method(), 'GET')
            self.assertIs(open_url.call_args.kwargs['context'], context)


if __name__ == '__main__':
    unittest.main()
