"""Ownership rules shared by manual bootstrap and Argo's read-only guard."""

REPOSITORY = 'https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments.git'
OWNER_LABEL = 'gitops/deployment-id'


def check_existing(expected, existing):
    owner = expected['metadata']['labels'][OWNER_LABEL]
    if existing.get('metadata', {}).get('labels', {}).get(OWNER_LABEL) != owner:
        raise ValueError(f'Refusing to take over {expected["kind"]}/{expected["metadata"]["name"]}: missing or different deployment owner')
    if expected['kind'] == 'Application':
        actual = existing.get('spec', {})
        desired = expected['spec']
        if (actual.get('source', {}).get('repoURL') != REPOSITORY
                or actual.get('source', {}).get('path') != desired['source']['path']
                or actual.get('project') != desired['project']
                or actual.get('destination') != desired['destination']):
            raise ValueError('Existing Application targets a different repository, path, project or destination')
