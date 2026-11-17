"""Print the parent configuration, or install it once with --install."""

import argparse
import json
import subprocess
from pathlib import Path

from bootstrap import preflight
from ownership import OWNER_LABEL, REPOSITORY

PARENT_NAME = 'cd-bootstrap'
GUARD_NAME = 'cd-bootstrap-guard'
GUARD_IMAGE = 'docker.io/library/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90'


def parent_manifest(root):
    labels = {OWNER_LABEL: PARENT_NAME}

    def item(api, kind, name, namespaced=True):
        metadata = {'name': name, 'labels': labels.copy()}
        if namespaced:
            metadata['namespace'] = 'argocd'
        return {'apiVersion': api, 'kind': kind, 'metadata': metadata}

    account = item('v1', 'ServiceAccount', GUARD_NAME)
    role = item('rbac.authorization.k8s.io/v1', 'ClusterRole', GUARD_NAME, False)
    role['rules'] = [
        {'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['get']},
        {'apiGroups': [''], 'resources': ['configmaps'],
         'resourceNames': ['app-settings', 'grafana-provisioning'], 'verbs': ['get']},
        {'apiGroups': [''], 'resources': ['services'], 'resourceNames': ['app', 'loki', 'grafana'], 'verbs': ['get']},
        {'apiGroups': ['apps'], 'resources': ['deployments'], 'resourceNames': ['app', 'loki', 'grafana'], 'verbs': ['get']},
        {'apiGroups': ['argoproj.io'], 'resources': ['applications', 'appprojects'], 'verbs': ['get']},
    ]
    binding = item('rbac.authorization.k8s.io/v1', 'ClusterRoleBinding', GUARD_NAME, False)
    binding['roleRef'] = {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': GUARD_NAME}
    binding['subjects'] = [{'kind': 'ServiceAccount', 'name': GUARD_NAME, 'namespace': 'argocd'}]
    code = item('v1', 'ConfigMap', GUARD_NAME)
    code['data'] = {name: (root / 'scripts' / name).read_text() for name in ('guard.py', 'ownership.py')}
    project = item('argoproj.io/v1alpha1', 'AppProject', PARENT_NAME)
    project['spec'] = {
        'sourceRepos': [REPOSITORY],
        'destinations': [{'server': 'https://kubernetes.default.svc', 'namespace': 'argocd'}],
        'clusterResourceWhitelist': [{'group': '', 'kind': 'Namespace'}],
        'namespaceResourceWhitelist': [{'group': 'argoproj.io', 'kind': kind} for kind in ('Application', 'AppProject')]
                                      + [{'group': 'batch', 'kind': 'Job'}],
    }
    application = item('argoproj.io/v1alpha1', 'Application', PARENT_NAME)
    application['spec'] = {
        'project': PARENT_NAME,
        'source': {'repoURL': REPOSITORY, 'targetRevision': 'main', 'path': 'bootstrap',
                   'directory': {'include': 'cd-*.json'}},
        'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'argocd'},
        'syncPolicy': {'automated': {'prune': False, 'selfHeal': True},
                       'syncOptions': ['FailOnSharedResource=true'],
                       'retry': {'limit': 3, 'backoff': {'duration': '5s', 'factor': 2, 'maxDuration': '30s'}}},
    }
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [account, role, binding, code, project, application]}


def install(root):
    manifest = parent_manifest(root)
    preflight(manifest['items'])
    subprocess.run(['kubectl', '--context', 'minikube', 'apply', '-f', '-'],
                   input=json.dumps(manifest), text=True, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='preflight and apply the parent configuration to minikube')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.install:
        try:
            install(root)
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
            raise SystemExit(getattr(error, 'stderr', None) or str(error))
    else:
        print(json.dumps(parent_manifest(root), indent=2))
