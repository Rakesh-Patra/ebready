"""Deploy a previously scanned immutable image using the runner's OIDC session."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.request import urlopen
from urllib.parse import urljoin, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

import boto3


def create_standard_version(eb, app, version, bucket, key):
    # A Dockerrun-only bundle has no env.yaml or .ebextensions to pre-process.
    # Standard deployment validates it on the instance; build/scan gates ran in CI.
    result = eb.create_application_version(ApplicationName=app, VersionLabel=version,
        SourceBundle={'S3Bucket': bucket, 'S3Key': key}, Process=False)
    status = str(result.get('ApplicationVersion', {}).get('Status', '')).casefold()
    if status == 'failed':
        raise SystemExit('Standard application version was rejected; environment was not changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@sha256:[0-9a-f]{64}', args.image):
        parser.error('An immutable private ECR image digest is required.')
    app, env, bucket = [os.environ[name] for name in ['EB_APPLICATION', 'EB_ENVIRONMENT', 'EB_ARTIFACT_BUCKET']]
    version = f"release-{os.environ['GITHUB_SHA'][:12]}-{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    session = boto3.Session()
    eb, s3 = session.client('elasticbeanstalk'), session.client('s3')
    current = eb.describe_environments(ApplicationName=app, EnvironmentNames=[env])['Environments']
    if len(current) != 1 or current[0]['Status'] != 'Ready':
        raise SystemExit('Target environment is not Ready. Inspect it before retrying.')
    previous = current[0].get('VersionLabel')
    output = Path('artifacts'); output.mkdir(exist_ok=True)
    receipt = {'startedAt': datetime.now(timezone.utc).isoformat(), 'application': app, 'environment': env,
               'version': version, 'previousVersion': previous, 'image': args.image, 'commit': os.environ['GITHUB_SHA'],
               'runUrl': f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}", 'verified': False}
    receipt_file = output / f'release-{version}.json'
    receipt_file.write_text(json.dumps(receipt, indent=2))
    bundle = output / 'release.zip'
    with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('Dockerrun.aws.json', json.dumps({'AWSEBDockerrunVersion': '1', 'Image': {'Name': args.image, 'Update': 'true'}, 'Ports': [{'ContainerPort': '8080'}]}))
    key = f'releases/{version}.zip'
    s3.upload_file(str(bundle), bucket, key, ExtraArgs={'ServerSideEncryption': 'AES256'})
    create_standard_version(eb, app, version, bucket, key)
    eb.update_environment(EnvironmentName=env, VersionLabel=version)
    for _ in range(180):
        row = eb.describe_environments(ApplicationName=app, EnvironmentNames=[env])['Environments'][0]
        if row['Status'] == 'Ready':
            if row.get('VersionLabel') != version or row.get('Health') != 'Green':
                raise SystemExit('Deployment is not Ready/Green on the requested version. Use the recorded previous version for a reviewed rollback.')
            break
        time.sleep(10)
    else:
        raise SystemExit('Deployment is still processing. Inspect events before retrying.')
    url = 'http://' + row['CNAME']
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
    receipt.update(verified=True, verifiedAt=datetime.now(timezone.utc).isoformat(), url=url, awsStatus=row['Status'], awsHealth=row['Health'], httpsConfigured=False)
    receipt_file.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write(f'### Verified public demo\n\n{url}\n\nVersion `{version}` is Ready/Green; homepage, health and JavaScript asset returned HTTP 200. Login is disabled until HTTPS is configured.\n')


if __name__ == '__main__':
    main()
