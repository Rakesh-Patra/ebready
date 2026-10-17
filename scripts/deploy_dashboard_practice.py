"""Deploy the reviewed dashboard bundle to a disposable standard EB environment.

Creates billable AWS resources. Run only for an authorized practice deployment.
Keeps hosted cloud controls disabled and sends only public InsForge settings.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import boto3

from package_dashboard import ROOT, package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--region', default='us-east-2')
    parser.add_argument('--deploy', action='store_true', help='Create/update billable AWS resources')
    args = parser.parse_args()
    if not args.deploy:
        parser.error('Pass --deploy only after reviewing the Dockerfile and practice runbook.')
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client('sts').get_caller_identity()['Account']
    eb = session.client('elasticbeanstalk')
    app = 'ebkit-dashboard-practice'
    environment = app
    prefix = f'{app}-{account}'
    existing = eb.describe_environments(EnvironmentNames=[environment])['Environments']
    if existing and existing[0]['Status'] != 'Ready':
        raise SystemExit(f"Existing environment is {existing[0]['Status']}; inspect it before retrying.")
    if not existing and not eb.check_dns_availability(CNAMEPrefix=prefix)['Available']:
        raise SystemExit('Practice hostname is unavailable; inspect ownership before retrying.')
    stacks = [s for s in eb.list_available_solution_stacks()['SolutionStacks']
              if 'Amazon Linux 2023' in s and 'running Docker' in s and 'ECS' not in s]
    if not stacks:
        raise SystemExit('No standard Amazon Linux 2023 Docker platform is available.')
    public = {}
    env_file = ROOT / 'web/server/.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep and key in {'INSFORGE_URL', 'INSFORGE_ANON_KEY'}:
                public[key] = value.strip().strip('\"\'')
    if not all(public.get(key) for key in ('INSFORGE_URL', 'INSFORGE_ANON_KEY')):
        raise SystemExit('Set public INSFORGE_URL and INSFORGE_ANON_KEY in web/server/.env first.')
    source = package(ROOT / 'artifacts/ebkit-dashboard.zip')
    version = 'practice-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    bucket = eb.create_storage_location()['S3Bucket']
    key = f'{app}/{version}.zip'
    session.client('s3').upload_file(str(source), bucket, key)
    if not eb.describe_applications(ApplicationNames=[app])['Applications']:
        eb.create_application(ApplicationName=app, Description='EBKit dashboard DevOps practice')
    eb.create_application_version(ApplicationName=app, VersionLabel=version,
                                  SourceBundle={'S3Bucket': bucket, 'S3Key': key}, Process=True)
    if existing:
        result = eb.update_environment(EnvironmentName=environment, VersionLabel=version)
    else:
        origin = f'http://{prefix}.{args.region}.elasticbeanstalk.com'
        public.update(EBKIT_WEB_ORIGINS=origin, EBKIT_LIVE_ENABLED='false', EBKIT_GITHUB_AUTO_DEPLOY='false')
        settings = [
            {'Namespace': 'aws:elasticbeanstalk:environment', 'OptionName': 'EnvironmentType', 'Value': 'SingleInstance'},
            {'Namespace': 'aws:autoscaling:launchconfiguration', 'OptionName': 'IamInstanceProfile', 'Value': 'aws-elasticbeanstalk-ec2-role'},
            {'Namespace': 'aws:ec2:instances', 'OptionName': 'InstanceTypes', 'Value': 't3.small'},
        ]
        settings += [{'Namespace': 'aws:elasticbeanstalk:application:environment', 'OptionName': name, 'Value': value}
                     for name, value in public.items()]
        result = eb.create_environment(ApplicationName=app, EnvironmentName=environment,
                                       CNAMEPrefix=prefix, VersionLabel=version,
                                       SolutionStackName=stacks[0], OptionSettings=settings,
                                       Tags=[{'Key': 'Purpose', 'Value': 'devops-practice'}])
    # No credentials, environment values or account secrets are written to receipts.
    receipt = f"Application: {app}\nEnvironment: {environment}\nRegion: {args.region}\nVersion: {version}\nStatus: {result['Status']}\nURL: http://{result.get('CNAME', '')}\n"
    (ROOT / 'artifacts/dashboard-deployment.txt').write_text(receipt, encoding='utf-8')
    print(receipt)


if __name__ == '__main__':
    main()
