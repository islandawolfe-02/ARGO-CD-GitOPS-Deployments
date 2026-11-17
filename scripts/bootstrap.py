"""Manually register one additional deployment in the existing minikube cluster."""

import json
import re
import subprocess
import sys
from pathlib import Path

from ownership import check_existing


def preflight(items):
    # Preflight every object before applying any of them.
    for item in items:
        args = ['kubectl', '--context', 'minikube', 'get', item['kind'], item['metadata']['name'],
                '-o', 'json', '--ignore-not-found']
        namespace = item['metadata'].get('namespace')
        if namespace:
            args.extend(['-n', namespace])
        result = subprocess.run(args, check=True, capture_output=True, text=True)
        if result.stdout.strip():
            check_existing(item, json.loads(result.stdout))


def bootstrap(root, identity):
    if not re.fullmatch(r'cd-[0-9a-f]{16}', identity):
        raise ValueError('Use the deployment ID printed by the receiving workflow')
    path = root / 'bootstrap' / (identity + '.json')
    items = [item for item in json.loads(path.read_text())['items']
             if item.get('metadata', {}).get('annotations', {}).get('argocd.argoproj.io/hook') != 'PreSync']
    resources = root / items[2]['spec']['source']['path'] / 'resources.json'
    preflight(items + json.loads(resources.read_text())['items'])
    subprocess.run(['kubectl', '--context', 'minikube', 'apply', '-f', '-'],
                   input=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}), text=True, check=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 scripts/bootstrap.py <deployment-id>')
    try:
        bootstrap(Path(__file__).resolve().parents[1], sys.argv[1])
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(getattr(error, 'stderr', None) or str(error))
