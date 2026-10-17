"""Move selected existing server credentials into Secrets Manager without printing values.

AWS credentials are deliberately excluded: the container uses an instance role.
Terraform receives only resource ARNs, never the secret payloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import boto3
from botocore.exceptions import ClientError
import click

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ebkit.aws_tls import configure_aws_tls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', action='store_true')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--region', default='us-east-2')
    args = parser.parse_args()
    values = {}
    for line in (ROOT / 'web/server/.env').read_text(encoding='utf-8').splitlines():
        key, sep, value = line.partition('=')
        if sep and key in {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'INSFORGE_API_KEY', 'EBKIT_GITHUB_TOKEN'}:
            value = value.strip().strip('\"\'')
            if value:
                values['GEMINI_API_KEY' if key == 'GOOGLE_API_KEY' else key] = value
    print('Server secret names present:', ', '.join(sorted(values)) or 'none')
    if not args.store:
        return
    if not values:
        raise SystemExit('No existing server credentials were found; no empty secrets were created.')
    arns = {}
    with click.Context(click.Command('store-secrets')) as ctx:
        configure_aws_tls(ctx)
        sm = boto3.Session(profile_name=args.profile, region_name=args.region).client('secretsmanager')
        for key, value in values.items():
            name = 'ebkit/dashboard-practice/' + key.lower().replace('_', '-')
            token = hashlib.sha256(value.encode()).hexdigest()
            try:
                secret = sm.describe_secret(SecretId=name)
                tags = {row['Key']: row['Value'] for row in secret.get('Tags', [])}
                if tags.get('Project') != 'EBKit' or tags.get('Purpose') != 'hackathon-demo':
                    raise SystemExit('Existing secret ownership did not match; no update was made.')
                sm.put_secret_value(SecretId=name, SecretString=value, ClientRequestToken=token)
                arns[key] = secret['ARN']
            except ClientError as error:
                if error.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
                result = sm.create_secret(Name=name, Description='EBKit server-only runtime credential',
                    SecretString=value, ClientRequestToken=token,
                    Tags=[{'Key': 'Project', 'Value': 'EBKit'}, {'Key': 'Purpose', 'Value': 'hackathon-demo'}])
                arns[key] = result['ARN']
    target = ROOT / 'infra/terraform/deployment.tfvars.json'
    config = json.loads(target.read_text(encoding='utf-8-sig'))
    config['secret_arns'] = arns
    target.write_text(json.dumps(config, indent=2), encoding='utf-8')
    print('Stored', len(arns), 'server credentials. Terraform input contains only their ARNs. No AWS access keys were stored.')


if __name__ == '__main__':
    main()
