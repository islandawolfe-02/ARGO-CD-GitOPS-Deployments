"""Render a registered application's additional minikube deployment."""

import hashlib
import json
import os
import re
from pathlib import Path

REPOSITORY = 'https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments.git'
OWNER_LABEL = 'gitops/deployment-id'
PORT_KEYS = ('app_port', 'grafana_port', 'loki_port')
INPUT_KEYS = {'project', 'source_repository', 'source_revision', 'image', 'namespace',
              'deployment_name', 'lab_env', *PORT_KEYS}


def validate(root, request):
    if set(request) != INPUT_KEYS or any(not isinstance(value, str) for value in request.values()):
        raise ValueError('Supply exactly the documented string inputs')
    for key in ('project', 'namespace', 'deployment_name'):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', request[key]):
            raise ValueError(f'Invalid Kubernetes name: {key}')
    namespace = request['namespace']
    if namespace in ('default', 'argocd', 'observability', 'p1-islandawolfe-02') or namespace.startswith('kube-'):
        raise ValueError('This namespace is reserved; choose an additional deployment namespace')
    profile = json.loads((root / 'projects' / (request['project'] + '.json')).read_text())
    if request['source_repository'] != profile['source_repository']:
        raise ValueError('Source repository does not match the registered project')
    image_name = 'ghcr.io/' + profile['source_repository'].lower()
    if not re.fullmatch(re.escape(image_name) + r'@sha256:[0-9a-f]{64}', request['image']):
        raise ValueError('Image must be the registered app package pinned by SHA-256 digest')
    if not re.fullmatch(r'[0-9a-f]{40}', request['source_revision']):
        raise ValueError('Source revision must be a full commit SHA')
    if request['lab_env'] not in profile['environments']:
        raise ValueError('Environment does not match the registered project')
    ports = [request[key] for key in PORT_KEYS]
    if any(not re.fullmatch(r'[1-9][0-9]{3,4}', port) or not 1024 <= int(port) <= 65535 for port in ports):
        raise ValueError('Local ports must be between 1024 and 65535')
    if len(set(ports)) != 3 or set(ports) & {'18080', '18081', '18082', '3000', '3100'}:
        raise ValueError('Ports must differ and avoid the original project ports')
    destination = root / 'deployments' / request['project'] / namespace / request['deployment_name']
    for path in (root / 'deployments').glob('*/*/*/record.json'):
        other = json.loads(path.read_text())
        if path.parent == destination:
            if other['source_repository'] != request['source_repository']:
                raise ValueError('Existing deployment belongs to a different source repository')
            continue
        if other['namespace'] == namespace:
            raise ValueError('Each installation must have its own namespace')
        if set(ports) & {other[key] for key in PORT_KEYS}:
            raise ValueError('A local port is already assigned to another recorded deployment')
    return profile, destination


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def validate_platforms(index):
    for architecture in ('amd64', 'arm64'):
        matches = [item.get('digest', '') for item in index.get('manifests', [])
                   if item.get('platform', {}).get('os') == 'linux'
                   and item.get('platform', {}).get('architecture') == architecture]
        if len(matches) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', matches[0]):
            raise ValueError(f'Registry image must contain one valid linux/{architecture} manifest')


def render(root, request):
    profile, destination = validate(root, request)
    namespace = request['namespace']
    identity = 'cd-' + hash_value([request[key] for key in ('project', 'namespace', 'deployment_name')])
    labels = {OWNER_LABEL: identity}
    session = identity
    resources = []

    def resource(kind, name, spec=None, data=None):
        item = {'apiVersion': 'apps/v1' if kind == 'Deployment' else 'v1', 'kind': kind,
                'metadata': {'name': name, 'namespace': namespace, 'labels': labels.copy()}}
        if spec is not None:
            item['spec'] = spec
        if data is not None:
            item['data'] = data
        resources.append(item)
        return item

    def deployment(name, container, config=None):
        selector = {'app.kubernetes.io/name': name, OWNER_LABEL: identity}
        template = {'metadata': {'labels': selector}, 'spec': {'containers': [container]}}
        if config is not None:
            template['metadata']['annotations'] = {'gitops/config-hash': hash_value(config)}
        return resource('Deployment', name, {'replicas': 1, 'selector': {'matchLabels': selector},
                                             'template': template})

    def service(name, port):
        resource('Service', name, {'selector': {'app.kubernetes.io/name': name, OWNER_LABEL: identity},
                                  'ports': [{'name': 'http', 'port': port, 'targetPort': 'http'}]})

    def probe(path):
        return {'httpGet': {'path': path, 'port': 'http'}, 'periodSeconds': 5}

    port = profile['container_port']
    settings = {**profile['environment'], 'LAB_ENV': request['lab_env'], 'LAB_SESSION': session,
                'ASPNETCORE_URLS': f'http://0.0.0.0:{port}',
                'LOCAL_LOKI_URL': f'http://loki.{namespace}.svc.cluster.local:3101'}
    resource('ConfigMap', 'app-settings', data=settings)
    deployment('app', {'name': 'app', 'image': request['image'], 'imagePullPolicy': 'IfNotPresent',
                       'ports': [{'name': 'http', 'containerPort': port}],
                       'envFrom': [{'configMapRef': {'name': 'app-settings'}}],
                       'readinessProbe': probe(profile['health_path']),
                       'livenessProbe': probe(profile['health_path'])}, settings)
    service('app', port)

    deployment('loki', {'name': 'loki', 'image': 'docker.io/grafana/loki:3.7.8',
                        'args': ['-config.file=/etc/loki/local-config.yaml',
                                 '-server.http-listen-port=3101', '-server.grpc-listen-port=9096'],
                        'ports': [{'name': 'http', 'containerPort': 3101}],
                        'readinessProbe': probe('/ready')})
    service('loki', 3101)

    dashboard = {
        'uid': 'cd-requests', 'title': request['project'] + ' CD requests',
        'time': {'from': 'now-1h', 'to': 'now'}, 'schemaVersion': 39,
        'templating': {'list': [
            {'name': 'session', 'type': 'textbox', 'label': 'Deployment session', 'query': session},
            {'name': 'request', 'type': 'textbox', 'label': 'Request or action ID', 'query': ''}]},
        'panels': [{'id': 1, 'type': 'logs', 'title': 'Completion records',
                    'gridPos': {'h': 20, 'w': 24, 'x': 0, 'y': 0},
                    'datasource': {'type': 'loki', 'uid': 'loki'},
                    'targets': [{'refId': 'A', 'expr': '{session="$session"} |= "$request"'}],
                    'options': {'showTime': True, 'wrapLogMessage': True, 'prettifyLogMessage': True}}],
    }
    provisioning = {
        'datasource.yaml': json.dumps({'apiVersion': 1, 'datasources': [
            {'name': 'Loki', 'uid': 'loki', 'type': 'loki', 'access': 'proxy',
             'url': 'http://loki:3101', 'isDefault': True}]}),
        'provider.yaml': json.dumps({'apiVersion': 1, 'providers': [
            {'name': 'cd', 'type': 'file', 'options': {'path': '/var/lib/grafana/dashboards'}}]}),
        'requests.json': json.dumps(dashboard),
    }
    resource('ConfigMap', 'grafana-provisioning', data=provisioning)
    grafana = deployment('grafana', {
        'name': 'grafana', 'image': 'docker.io/grafana/grafana:13.2.3',
        'ports': [{'name': 'http', 'containerPort': 3001}],
        'env': [{'name': key, 'value': value} for key, value in {
            'GF_SERVER_HTTP_PORT': '3001', 'GF_AUTH_ANONYMOUS_ENABLED': 'true',
            'GF_AUTH_ANONYMOUS_ORG_ROLE': 'Viewer', 'GF_AUTH_DISABLE_LOGIN_FORM': 'true'}.items()],
        'readinessProbe': probe('/api/health'),
        'volumeMounts': [{'name': 'provisioning', 'subPath': filename, 'mountPath': path} for filename, path in (
            ('datasource.yaml', '/etc/grafana/provisioning/datasources/loki.yaml'),
            ('provider.yaml', '/etc/grafana/provisioning/dashboards/provider.yaml'),
            ('requests.json', '/var/lib/grafana/dashboards/requests.json'))],
    }, provisioning)
    grafana['spec']['template']['spec']['volumes'] = [
        {'name': 'provisioning', 'configMap': {'name': 'grafana-provisioning'}}]
    service('grafana', 3001)

    bootstrap = [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace, 'labels': labels}},
        {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'AppProject',
         'metadata': {'name': identity, 'namespace': 'argocd', 'labels': labels},
         'spec': {'sourceRepos': [REPOSITORY],
                  'destinations': [{'server': 'https://kubernetes.default.svc', 'namespace': namespace}],
                  'clusterResourceWhitelist': [],
                  'namespaceResourceWhitelist': [{'group': group, 'kind': kind} for group, kind in (
                      ('', 'ConfigMap'), ('', 'Service'), ('apps', 'Deployment'),
                      ('', 'Pod'), ('apps', 'ReplicaSet'))]}},
        {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
         'metadata': {'name': identity, 'namespace': 'argocd', 'labels': labels},
         'spec': {'project': identity,
                  'source': {'repoURL': REPOSITORY, 'targetRevision': 'main',
                             'path': str(destination.relative_to(root)),
                             'directory': {'include': 'resources.json'}},
                  'destination': {'server': 'https://kubernetes.default.svc', 'namespace': namespace},
                  'syncPolicy': {'automated': {'prune': False, 'selfHeal': True},
                                 'syncOptions': ['FailOnSharedResource=true']}}},
    ]
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        destination / 'resources.json': {'apiVersion': 'v1', 'kind': 'List', 'items': resources},
        destination / 'record.json': {**request, 'deployment_id': identity},
        root / 'bootstrap' / (identity + '.json'): {'apiVersion': 'v1', 'kind': 'List', 'items': bootstrap},
    }
    for path, value in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n')
    commands = [f'python3 scripts/bootstrap.py {identity}',
                f'kubectl --context minikube -n argocd get application {identity}',
                f'kubectl --context minikube -n {namespace} get pods,svc,endpointslices -o wide']
    for name, key, target in (('app', 'app_port', port), ('grafana', 'grafana_port', 3001), ('loki', 'loki_port', 3101)):
        commands.append(f'kubectl --context minikube -n {namespace} port-forward svc/{name} {request[key]}:{target}')
    (destination / 'commands.txt').write_text('\n'.join(commands) + '\n')
    return identity, destination


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    try:
        identity, destination = render(root, json.loads(os.environ['INPUTS_JSON']))
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(str(error))
    print('Prepared ' + str(destination.relative_to(root)))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'deployment_id={identity}\npath={destination.relative_to(root)}\n')
