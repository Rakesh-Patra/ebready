"""Move selected existing server credentials into Secrets Manager without printing values.

AWS credentials are deliberately excluded: the container uses a workload role.
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

ROLE_KEYS = {'EBKIT_ALLOWED_EMAILS', 'EBKIT_TESTING_EMAILS',
             'EBKIT_EVALUATOR_EMAILS', 'EBKIT_PAID_TEST_EMAILS'}


def read_runtime_values(path):
    """Keep role lists separate; never infer privileges from the approval list."""
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or key not in ROLE_KEYS | {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'INSFORGE_API_KEY', 'EBKIT_GITHUB_TOKEN'}:
            continue
        value = value.strip().strip('\"\'')
        if key in ROLE_KEYS:
            value = ','.join(sorted({email.strip().lower() for email in value.split(',') if email.strip()}))
            # Empty lists must also be stored so removing a role revokes it.
            values[key] = value
        elif value:
            destination = 'GEMINI_API_KEY' if key == 'GOOGLE_API_KEY' else key
            if key != 'GOOGLE_API_KEY' or destination not in values:
                values[destination] = value
    if ROLE_KEYS & values.keys():
        if not ROLE_KEYS <= values.keys():
            raise ValueError('Define all four email-role lists explicitly; use an empty value for unused roles.')
        approved = set(values['EBKIT_ALLOWED_EMAILS'].split(',')) - {''}
        for key in ROLE_KEYS - {'EBKIT_ALLOWED_EMAILS'}:
            if (set(values[key].split(',')) - {''}) - approved:
                raise ValueError('Every privileged email must also belong to the approved-email list.')
    return values


def merge_secret_references(config, arns):
    """Preserve existing references when updating only selected runtime secrets."""
    return {**config, 'secret_arns': {**config.get('secret_arns', {}), **arns}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', action='store_true')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--region', default='us-east-2')
    args = parser.parse_args()
    values = read_runtime_values(ROOT / 'web/server/.env')
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
    config = merge_secret_references(config, arns)
    target.write_text(json.dumps(config, indent=2), encoding='utf-8')
    print('Stored', len(arns), 'server credentials. Terraform input contains only their ARNs. No AWS access keys were stored.')


if __name__ == '__main__':
    main()
