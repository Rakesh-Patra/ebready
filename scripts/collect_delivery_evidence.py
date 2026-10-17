"""Collect deployment metadata without reading or recording secret values."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import boto3
import click

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ebkit.aws_tls import configure_aws_tls


def main():
    name, region = 'ebkit-dashboard-practice', 'us-east-2'
    evidence = {'capturedAt': datetime.now(timezone.utc).isoformat(), 'region': region}
    with click.Context(click.Command('collect-evidence')) as ctx:
        configure_aws_tls(ctx)
        session = boto3.Session(profile_name='default', region_name=region)
        eb, iam = session.client('elasticbeanstalk'), session.client('iam')
        row = eb.describe_environments(EnvironmentNames=[name])['Environments'][0]
        evidence['environment'] = {key: row.get(key) for key in ['EnvironmentName', 'EnvironmentId', 'Status', 'Health', 'HealthStatus', 'VersionLabel', 'CNAME', 'SolutionStackName']}
        configurations = eb.describe_configuration_settings(ApplicationName=name, EnvironmentName=name)['ConfigurationSettings'][0]['OptionSettings']
        evidence['runtimeSecretReferences'] = sorted(r['OptionName'] for r in configurations if r['Namespace'] == 'aws:elasticbeanstalk:application:environmentsecrets')
        role = iam.get_role(RoleName=name + '-github-deploy')['Role']
        evidence['oidcTrust'] = role['AssumeRolePolicyDocument']
        evidence['instanceRole'] = name + '-ec2'
        logs = session.client('logs').describe_log_groups(logGroupNamePrefix='/aws/elasticbeanstalk/' + name)['logGroups']
        evidence['logGroups'] = [{'name': r['logGroupName'], 'retentionDays': r.get('retentionInDays')} for r in logs]
        alarms = session.client('cloudwatch').describe_alarms(AlarmNamePrefix=name)['MetricAlarms']
        evidence['alarms'] = [{'name': r['AlarmName'], 'state': r['StateValue'], 'metric': r['MetricName'], 'notificationsConfigured': bool(r['AlarmActions'])} for r in alarms]
        secrets = session.client('secretsmanager').list_secrets(Filters=[{'Key': 'name', 'Values': ['ebkit/dashboard-practice/']}])['SecretList']
        evidence['storedSecretNames'] = sorted(r['Name'] for r in secrets)
        events = session.client('cloudtrail').lookup_events(LookupAttributes=[{'AttributeKey': 'EventName', 'AttributeValue': 'AssumeRoleWithWebIdentity'}], MaxResults=20)['Events']
        evidence['oidcSessions'] = []
        for event in events:
            detail = json.loads(event['CloudTrailEvent'])
            request = detail.get('requestParameters') or {}
            if request.get('roleArn') == role['Arn']:
                evidence['oidcSessions'].append({'time': event['EventTime'].isoformat(), 'session': request.get('roleSessionName'), 'event': event['EventName']})
    output = ROOT / 'artifacts/devops/infrastructure-evidence.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'environment': evidence['environment'], 'secretReferenceNames': evidence['runtimeSecretReferences'],
                      'logGroups': len(evidence['logGroups']), 'alarms': evidence['alarms'], 'oidcSessions': len(evidence['oidcSessions'])}, indent=2))


if __name__ == '__main__':
    main()
