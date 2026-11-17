"""Argo PreSync ownership check; authenticates using a read-only service account."""

import json
import os
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ownership import check_existing

RESOURCES = {
    'Namespace': ('v1', 'namespaces'),
    'ConfigMap': ('v1', 'configmaps'),
    'Service': ('v1', 'services'),
    'Deployment': ('apps/v1', 'deployments'),
    'Application': ('argoproj.io/v1alpha1', 'applications'),
    'AppProject': ('argoproj.io/v1alpha1', 'appprojects'),
}


def resource_path(item):
    version, plural = RESOURCES[item['kind']]
    path = '/api/' + version if version == 'v1' else '/apis/' + version
    namespace = item['metadata'].get('namespace')
    if namespace:
        path += '/namespaces/' + quote(namespace, safe='')
    return path + '/' + plural + '/' + quote(item['metadata']['name'], safe='')


def read_resource(item, token, context):
    request = Request('https://kubernetes.default.svc' + resource_path(item),
                      headers={'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, context=context, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f'Cannot check {resource_path(item)}: Kubernetes HTTP {error.code}') from None


def check_resources(items, read):
    for item in items:
        existing = read(item)
        if existing is not None:
            check_existing(item, existing)


if __name__ == '__main__':
    try:
        account = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        token = (account / 'token').read_text().strip()
        context = ssl.create_default_context(cafile=str(account / 'ca.crt'))
        check_resources(json.loads(os.environ['EXPECTED_RESOURCES']),
                        lambda item: read_resource(item, token, context))
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        raise SystemExit(str(error))
    print('Ownership checks passed; automatic registration may proceed.')
