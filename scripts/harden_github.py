"""Review/apply this repository's main-branch protection and secret protection."""
import argparse
import json
from pathlib import Path

from github_delivery import client, REPOSITORY, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    policy = json.loads((ROOT / 'security/main-protection.json').read_text())
    if not args.apply:
        print(json.dumps({'repository': REPOSITORY, 'branch': 'main', 'protection': policy,
                          'note': 'Preview only. --apply changes GitHub settings.'}, indent=2))
        return
    api = client()
    base = '/repos/' + REPOSITORY
    api('PUT', base + '/branches/main/protection', policy)
    api('PUT', base + '/vulnerability-alerts')
    api('PUT', base + '/automated-security-fixes')
    api('PATCH', base, {'security_and_analysis': {
        'secret_scanning': {'status': 'enabled'},
        'secret_scanning_push_protection': {'status': 'enabled'},
    }})
    actual = api('GET', base + '/branches/main/protection')
    repo = api('GET', base)
    receipt = {'repository': REPOSITORY, 'branch': 'main',
               'requiredChecks': actual['required_status_checks']['contexts'],
               'requiredReviews': actual['required_pull_request_reviews']['required_approving_review_count'],
               'codeOwnerReviews': actual['required_pull_request_reviews']['require_code_owner_reviews'],
               'enforceAdmins': actual['enforce_admins']['enabled'],
               'forcePushesAllowed': actual['allow_force_pushes']['enabled'],
               'deletionsAllowed': actual['allow_deletions']['enabled'],
               'dependabotSecurityUpdates': api('GET', base + '/automated-security-fixes'),
               'security': repo.get('security_and_analysis', {})}
    output = ROOT / 'artifacts/devops/github-protection.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
