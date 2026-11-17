import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bootstrap import bootstrap
from render import OWNER_LABEL, render, validate, validate_platforms


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'projects').mkdir()
        source = 'example/foundation'
        (self.root / 'projects/foundation.json').write_text(json.dumps({
            'source_repository': source, 'container_port': 8081,
            'environments': ['workshop-blue', 'workshop-green'],
            'health_path': '/healthz', 'environment': {'DOTNET_PROCESSOR_COUNT': '1'}}))
        self.request = {
            'project': 'foundation', 'source_repository': source, 'source_revision': 'a' * 40,
            'image': 'ghcr.io/example/foundation@sha256:' + 'b' * 64,
            'namespace': 'my-application', 'deployment_name': 'first', 'lab_env': 'workshop-green',
            'app_port': '18090', 'grafana_port': '3001', 'loki_port': '3101'}

    def test_manifest_isolation_and_port_routes(self):
        identity, directory = render(self.root, self.request)
        items = json.loads((directory / 'resources.json').read_text())['items']
        self.assertEqual(len(items), 8)
        for item in items:
            self.assertEqual(item['metadata']['namespace'], self.request['namespace'])
            self.assertEqual(item['metadata']['labels'][OWNER_LABEL], identity)
        for name, port in (('app', 8081), ('grafana', 3001), ('loki', 3101)):
            service = next(item for item in items if item['kind'] == 'Service' and item['metadata']['name'] == name)
            deployment = next(item for item in items if item['kind'] == 'Deployment' and item['metadata']['name'] == name)
            self.assertEqual(service['spec']['selector'], deployment['spec']['template']['metadata']['labels'])
            self.assertEqual(service['spec']['ports'][0]['port'], port)
            self.assertEqual(deployment['spec']['template']['spec']['containers'][0]['ports'][0]['containerPort'], port)
        settings = next(item['data'] for item in items if item['metadata']['name'] == 'app-settings')
        self.assertEqual(settings['LOCAL_LOKI_URL'], 'http://loki.my-application.svc.cluster.local:3101')
        self.assertEqual(settings['LAB_SESSION'], identity)

    def test_bootstrap_restricts_argo_to_one_namespace_and_resource_file(self):
        identity, directory = render(self.root, self.request)
        namespace, project, application = json.loads((self.root / f'bootstrap/{identity}.json').read_text())['items'][:3]
        self.assertEqual(namespace['metadata']['name'], 'my-application')
        self.assertEqual(project['spec']['clusterResourceWhitelist'], [])
        self.assertEqual(project['spec']['destinations'][0]['namespace'], 'my-application')
        self.assertEqual(application['spec']['source']['directory']['include'], 'resources.json')
        self.assertEqual(application['spec']['syncPolicy']['automated'], {'prune': False, 'selfHeal': True})
        self.assertEqual(application['spec']['source']['path'], str(directory.relative_to(self.root)))

    def test_repeated_request_is_stable_and_setting_changes_roll_the_app(self):
        _, directory = render(self.root, self.request)
        before = (directory / 'resources.json').read_text()
        render(self.root, self.request)
        self.assertEqual(before, (directory / 'resources.json').read_text())
        render(self.root, {**self.request, 'lab_env': 'workshop-blue'})
        after = json.loads((directory / 'resources.json').read_text())['items']
        before_items = json.loads(before)['items']
        old = next(item for item in before_items if item['kind'] == 'Deployment' and item['metadata']['name'] == 'app')
        new = next(item for item in after if item['kind'] == 'Deployment' and item['metadata']['name'] == 'app')
        self.assertNotEqual(old['spec']['template']['metadata']['annotations'], new['spec']['template']['metadata']['annotations'])

    def test_reserved_names_injection_and_invalid_identity_are_rejected(self):
        for key, value in (
            ('namespace', 'p1-islandawolfe-02'), ('namespace', 'argocd'), ('namespace', 'observability'),
            ('namespace', 'kube-system'), ('namespace', 'default'), ('namespace', '../escape'),
            ('deployment_name', 'bad\nname'), ('project', '../escape'), ('source_repository', 'other/app'),
            ('image', 'ghcr.io/example/foundation:latest'), ('source_revision', 'not-a-sha'),
            ('lab_env', 'production'), ('app_port', '3000'), ('app_port', '18081'),
            ('app_port', '3001'), ('app_port', '65536'), ('app_port', '018090')):
            with self.subTest(key=key, value=value), self.assertRaises((ValueError, FileNotFoundError)):
                validate(self.root, {**self.request, key: value})

    def test_conflicting_namespace_and_local_ports_are_rejected(self):
        render(self.root, self.request)
        with self.assertRaisesRegex(ValueError, 'own namespace'):
            render(self.root, {**self.request, 'deployment_name': 'second'})
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            render(self.root, {**self.request, 'namespace': 'another-namespace'})

    def test_additional_namespace_with_different_ports_is_allowed(self):
        render(self.root, self.request)
        render(self.root, {**self.request, 'namespace': 'another-namespace',
                           'app_port': '18091', 'grafana_port': '3002', 'loki_port': '3102'})
        self.assertEqual(len(list((self.root / 'deployments').glob('*/*/*/record.json'))), 2)

    def test_bootstrap_refuses_unowned_namespace_before_any_apply(self):
        identity, _ = render(self.root, self.request)
        existing = subprocess.CompletedProcess([], 0, stdout='{"metadata":{"labels":{}}}')
        with patch('bootstrap.subprocess.run', return_value=existing) as run:
            with self.assertRaisesRegex(ValueError, 'take over'):
                bootstrap(self.root, identity)
            self.assertEqual(run.call_count, 1)

    def test_bootstrap_checks_app_resources_before_apply(self):
        identity, _ = render(self.root, self.request)
        absent = subprocess.CompletedProcess([], 0, stdout='')
        unowned = subprocess.CompletedProcess([], 0, stdout='{"metadata":{"labels":{}}}')
        with patch('bootstrap.subprocess.run', side_effect=[absent] * 3 + [unowned]) as run:
            with self.assertRaisesRegex(ValueError, 'take over'):
                bootstrap(self.root, identity)
            self.assertFalse(any('apply' in call.args[0] for call in run.call_args_list))

    def test_bootstrap_applies_only_after_all_preflight_checks(self):
        identity, _ = render(self.root, self.request)
        with patch('bootstrap.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout='')) as run:
            bootstrap(self.root, identity)
            self.assertEqual(run.call_count, 12)
            self.assertIn('apply', run.call_args_list[-1].args[0])
            self.assertTrue(all(call.args[0][1:3] == ['--context', 'minikube'] for call in run.call_args_list))

    def test_registry_requires_both_platforms_and_ignores_attestations(self):
        index = {'manifests': [
            {'digest': 'sha256:' + 'c' * 64, 'platform': {'os': 'linux', 'architecture': 'amd64'}},
            {'digest': 'sha256:' + 'd' * 64, 'platform': {'os': 'linux', 'architecture': 'arm64'}},
            {'digest': 'sha256:' + 'e' * 64, 'platform': {'os': 'unknown', 'architecture': 'unknown'}}]}
        validate_platforms(index)
        with self.assertRaises(ValueError):
            validate_platforms({'manifests': index['manifests'][:1]})


if __name__ == '__main__':
    unittest.main()
