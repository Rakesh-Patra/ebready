"""Deploy a previously scanned immutable image using the runner's OIDC session."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit

import boto3


def create_cluster_version(eb, app, version, image):
    """Use the current AWS CLI when the installed SDK predates Cluster Mode."""
    import subprocess
    request = {'ApplicationName': app, 'VersionLabel': version,
               'ImageConfiguration': {'Source': {'Uri': image}}}
    if 'ImageConfiguration' in eb.meta.service_model.operation_model('CreateApplicationVersion').input_shape.members:
        result = eb.create_application_version(**request)
    else:
        completed = subprocess.run(['aws', 'elasticbeanstalk', 'create-application-version',
            '--cli-input-json', json.dumps(request), '--no-cli-pager'], capture_output=True, text=True)
        if completed.returncode:
            raise SystemExit('Cluster image version registration failed: ' + completed.stderr)
        result = json.loads(completed.stdout)
    if str(result.get('ApplicationVersion', {}).get('Status', '')).casefold() == 'failed':
        raise SystemExit('Cluster image version was rejected; environment was not changed.')


def update_cluster_stack(cfn, stack, version):
    # Terraform owns the template/configuration; CI changes only the version parameter.
    cfn.update_stack(StackName=stack, UsePreviousTemplate=True,
        Parameters=[{'ParameterKey': 'VersionLabel', 'ParameterValue': version}])
    cfn.get_waiter('stack_update_complete').wait(StackName=stack,
        WaiterConfig={'Delay': 15, 'MaxAttempts': 120})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@sha256:[0-9a-f]{64}', args.image):
        parser.error('An immutable private ECR image digest is required.')
    app, env, stack = [os.environ[name] for name in ['EB_APPLICATION', 'EB_ENVIRONMENT', 'EB_CLUSTER_STACK']]
    version = f"release-{os.environ['GITHUB_SHA'][:12]}-{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    session = boto3.Session()
    eb, cfn = session.client('elasticbeanstalk'), session.client('cloudformation')
    current = eb.describe_environments(ApplicationName=app, EnvironmentNames=[env])['Environments']
    if len(current) != 1 or current[0]['Status'] != 'Ready':
        raise SystemExit('Target environment is not Ready. Inspect it before retrying.')
    if current[0].get('Tier', {}).get('Type') != 'EKS':
        raise SystemExit('Delivery requires a Cluster environment; refusing Standard deployment.')
    previous = current[0].get('VersionLabel')
    output = Path('artifacts'); output.mkdir(exist_ok=True)
    receipt = {'startedAt': datetime.now(timezone.utc).isoformat(), 'application': app, 'environment': env,
               'mode': 'Cluster', 'version': version, 'previousVersion': previous, 'image': args.image, 'commit': os.environ['GITHUB_SHA'],
               'runUrl': f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}", 'verified': False}
    receipt_file = output / f'release-{version}.json'
    receipt_file.write_text(json.dumps(receipt, indent=2))
    create_cluster_version(eb, app, version, args.image)
    update_cluster_stack(cfn, stack, version)
    for _ in range(180):
        row = eb.describe_environments(ApplicationName=app, EnvironmentNames=[env])['Environments'][0]
        if row['Status'] == 'Ready':
            if row.get('VersionLabel') != version or row.get('Health') in ('Red', 'Yellow'):
                raise SystemExit('Deployment is not Ready on the requested version or reports unhealthy status. Use the recorded previous version for a reviewed rollback.')
            break
        time.sleep(10)
    else:
        raise SystemExit('Deployment is still processing. Inspect events before retrying.')
    url = 'https://' + row['CNAME']
    with urlopen('http://' + row['CNAME'] + '/api/health', timeout=20) as response:
        redirect = urlsplit(response.geturl())
        assert redirect.scheme == 'https' and redirect.hostname == row['CNAME'] and redirect.port in (None, 443) and redirect.path == '/api/health', 'HTTP does not redirect to the verified HTTPS origin.'
        receipt['httpRedirectsToHttps'] = True
    with urlopen(url, timeout=20) as response:
        html = response.read().decode()
        receipt['homepageStatus'] = response.status
    with urlopen(url + '/api/health', timeout=20) as response:
        health = json.load(response)
        assert response.status == 200 and health['liveEnabled'] is False
        receipt['healthStatus'] = response.status
    asset = re.search(r'<script[^>]+src="([^"]+)"', html)
    assert asset, 'No built JavaScript asset was served.'
    asset_url = urljoin(url + '/', asset[1])
    assert urlsplit(asset_url).netloc == urlsplit(url).netloc
    with urlopen(asset_url, timeout=20) as response:
        assert response.status == 200
        receipt['assetStatus'] = response.status
    # An empty sign-in must reach InsForge validation without creating an account.
    request = Request(url + '/insforge/api/auth/sessions', data=b'{}',
                      headers={'Content-Type': 'application/json', 'Origin': url}, method='POST')
    try:
        with urlopen(request, timeout=30) as response:
            status, body = response.status, response.read().decode()
    except HTTPError as error:
        status, body = error.code, error.read().decode()
    assert status in (400, 401, 422) and 'public demo' not in body.lower(), 'Authentication proxy is unavailable.'
    receipt['loginValidationStatus'] = status
    assert '"publicDemo":false' in html, 'Login is still disabled in runtime configuration.'
    receipt.update(verified=True, verifiedAt=datetime.now(timezone.utc).isoformat(), url=url, awsStatus=row['Status'], awsHealth=row['Health'], httpsConfigured=True)
    receipt_file.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write(f'### Verified HTTPS Cluster release\n\n{url}\n\nVersion `{version}` is Ready/{row.get('Health')}; homepage, health and JavaScript asset returned HTTP 200. HTTPS and the authentication proxy are enabled; live cloud operations remain disabled.\n')


if __name__ == '__main__':
    main()
