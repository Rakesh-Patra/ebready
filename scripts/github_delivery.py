"""Configure repository deployment variables using Git Credential Manager in memory.

The credential is never printed or written. Only this repository is contacted.
"""
import argparse
import json
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

REPOSITORY = 'Rakesh-Patra/ebready'
ROOT = Path(__file__).resolve().parents[1]


def client():
    credential = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                                capture_output=True, text=True, check=True)
    fields = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
    token = fields.get('password')
    if not token:
        raise SystemExit('No GitHub credential is available through Git Credential Manager.')

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
            if redirected and urlsplit(newurl).netloc != 'api.github.com':
                redirected.remove_header('Authorization')
            return redirected
    opener = urllib.request.build_opener(SafeRedirect())

    def request(method, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request('https://api.github.com' + path, data=data, method=method,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=30) as response:
                payload = response.read()
                return payload if raw else json.loads(payload or '{}')
        except urllib.error.HTTPError as error:
            detail = json.loads(error.read() or '{}').get('message', 'GitHub request failed')
            raise RuntimeError(f'GitHub {method} {path}: HTTP {error.code}: {detail}') from None
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['inspect', 'configure', 'runs'])
    parser.add_argument('--branch', default='feat/modern-workspace-deployment-improvements')
    args = parser.parse_args()
    api = client()
    base = f'/repos/{REPOSITORY}'
    if args.operation == 'inspect':
        repo = api('GET', base)
        print(json.dumps({'repository': repo['full_name'], 'default_branch': repo['default_branch'],
                          'permissions': repo.get('permissions', {})}))
    elif args.operation == 'runs':
        result = api('GET', base + '/actions/runs?per_page=5')
        print(json.dumps([{key: row.get(key) for key in ['id', 'name', 'head_sha', 'status', 'conclusion', 'html_url']}
                          for row in result['workflow_runs']], indent=2))
    else:
        subject = api('GET', base + '/actions/oidc/customization/sub')
        prefix = subject.get('sub_claim_prefix')
        if prefix:
            config_path = ROOT / 'infra/terraform/deployment.tfvars.json'
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
            config['github_subject_prefix'] = prefix
            config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        output = json.loads((ROOT / 'artifacts/devops/terraform-outputs.json').read_text(encoding='utf-8-sig'))
        values = {'AWS_REGION': 'us-east-2', 'AWS_ROLE_ARN': output['github_role_arn']['value'],
                  'EB_ARTIFACT_BUCKET': output['artifact_bucket']['value'],
                  'ECR_REPOSITORY': output['ecr_repository']['value'],
                  'EB_APPLICATION': output['application_name']['value'],
                  'EB_ENVIRONMENT': output['environment_name']['value'],
                  'DEPLOYMENT_BRANCH': args.branch}
        api('PUT', base + '/environments/production', {'deployment_branch_policy': {
            'protected_branches': False, 'custom_branch_policies': True}})
        policies = api('GET', base + '/environments/production/deployment-branch-policies')['branch_policies']
        for name in ['main', args.branch]:
            if not any(row['name'] == name for row in policies):
                api('POST', base + '/environments/production/deployment-branch-policies', {'name': name, 'type': 'branch'})
        existing = {row['name'] for row in api('GET', base + '/actions/variables?per_page=100')['variables']}
        for name, value in values.items():
            api('PATCH' if name in existing else 'POST',
                base + '/actions/variables' + ('/' + name if name in existing else ''), {'name': name, 'value': value})
        print('Configured production environment branch restrictions and seven non-secret repository variables. No AWS access keys stored.')


if __name__ == '__main__':
    main()
