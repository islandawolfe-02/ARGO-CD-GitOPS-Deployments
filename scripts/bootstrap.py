"""Manually register one additional deployment in the existing minikube cluster."""

import json
import re
import subprocess
import sys
from pathlib import Path

from render import OWNER_LABEL, REPOSITORY


def check_existing(expected, existing):
    if existing.get('metadata', {}).get('labels', {}).get(OWNER_LABEL) != expected['metadata']['labels'][OWNER_LABEL]:
        raise ValueError('Refusing to take over an existing namespace, project or Application')
    if expected['kind'] == 'Application':
        if existing['spec']['source']['repoURL'] != REPOSITORY or existing['spec']['destination'] != expected['spec']['destination']:
            raise ValueError('Existing Application targets a different repository or destination')


def bootstrap(root, identity):
    if not re.fullmatch(r'cd-[0-9a-f]{16}', identity):
        raise ValueError('Use the deployment ID printed by the receiving workflow')
    path = root / 'bootstrap' / (identity + '.json')
    items = json.loads(path.read_text())['items']
    resources = root / items[2]['spec']['source']['path'] / 'resources.json'
    items += json.loads(resources.read_text())['items']
    # Preflight every object before applying any of them.
    for item in items:
        args = ['kubectl', '--context', 'minikube', 'get', item['kind'], item['metadata']['name'],
                '-o', 'json', '--ignore-not-found']
        if item['kind'] != 'Namespace':
            args.extend(['-n', item['metadata']['namespace']])
        result = subprocess.run(args, check=True, capture_output=True, text=True)
        if result.stdout.strip():
            check_existing(item, json.loads(result.stdout))
    subprocess.run(['kubectl', '--context', 'minikube', 'apply', '-f', str(path)], check=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 scripts/bootstrap.py <deployment-id>')
    try:
        bootstrap(Path(__file__).resolve().parents[1], sys.argv[1])
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
