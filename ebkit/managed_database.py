"""Prepare private PostgreSQL before the first application deployment.

The operator selects an existing Beanstalk cluster network; users never supply
AWS credentials or receive the generated database password.
"""
import json
import re
from urllib.parse import quote

import click
from botocore.exceptions import ClientError

DATABASE_VARIABLES = {'DATABASE_URL', 'POSTGRES_URL', 'POSTGRESQL_URL'}


def validate_plan(data):
    if not isinstance(data, dict):
        raise click.ClickException('Managed database settings must be a JSON object.')
    if set(data) - {'clusterName', 'subnetIds', 'instanceClass', 'storageGb', 'multiAz', 'variables'}:
        raise click.ClickException('Unsupported managed database setting.')
    names = data.get('variables', ['DATABASE_URL'])
    if not isinstance(names, list) or not names or any(not isinstance(name, str) or name not in DATABASE_VARIABLES for name in names) or len(names) != len(set(names)):
        raise click.ClickException('Choose DATABASE_URL, POSTGRES_URL or POSTGRESQL_URL for managed PostgreSQL.')
    if not isinstance(data.get('clusterName'), str) or not re.fullmatch(r'[A-Za-z0-9][-A-Za-z0-9_]{0,99}', data['clusterName']):
        raise click.ClickException('Provide the existing Beanstalk EKS clusterName.')
    subnets = data.get('subnetIds')
    if not isinstance(subnets, list) or not 2 <= len(subnets) <= 16 or any(not isinstance(item, str) or not re.fullmatch(r'subnet-[a-f0-9]+', item) for item in subnets) or len(set(subnets)) != len(subnets):
        raise click.ClickException('Provide distinct database subnetIds in at least two availability zones.')
    size = data.get('storageGb', 20)
    if type(size) is not int or not 20 <= size <= 1000:
        raise click.ClickException('storageGb must be an integer between 20 and 1000.')
    if type(data.get('multiAz', False)) is not bool:
        raise click.ClickException('multiAz must be true or false.')
    if not isinstance(data.get('instanceClass', 'db.t4g.micro'), str) or not re.fullmatch(r'db\.[a-z0-9]+\.[a-z0-9]+', data.get('instanceClass', 'db.t4g.micro')):
        raise click.ClickException('Invalid RDS instance class.')
    return {**data, 'variables': names}


def prepare_database(session, app, env, record, plan):
    from ebkit.commands import production_ops as ops
    plan = validate_plan(plan)
    cluster = session.client('eks').describe_cluster(name=plan['clusterName'])['cluster']
    if cluster.get('status') != 'ACTIVE':
        raise click.ClickException('The operator-selected Beanstalk cluster must be ACTIVE.')
    network = cluster['resourcesVpcConfig']
    subnets = network.get('subnetIds', [])
    if not subnets or not network.get('clusterSecurityGroupId'):
        raise click.ClickException('The cluster network is unavailable.')
    # Reuse this exact subnet selection when launching the Beanstalk app.
    if record.get('database_cluster_name') and record['database_cluster_name'] != plan['clusterName']:
        raise click.ClickException('A managed database cannot be moved to a different cluster during redeployment.')
    record['database_cluster_name'] = plan['clusterName']
    data = {key: plan[key] for key in ('subnetIds', 'instanceClass', 'storageGb', 'multiAz') if key in plan}
    data.update(vpcId=network['vpcId'], applicationSecurityGroup=network['clusterSecurityGroupId'])
    try:
        db = ops.owned_database(session, app)
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') != 'DBInstanceNotFound':
            raise
        click.echo('Creating private PostgreSQL. It remains billable until separately deleted.')
        ops.database_create(session, session.client('elasticbeanstalk'), app, env, record, data)
        db = None
    if not db or db.get('DBInstanceStatus') != 'available':
        click.echo('Waiting for PostgreSQL before deploying the application...')
        session.client('rds').get_waiter('db_instance_available').wait(
            DBInstanceIdentifier=ops.db_id(app), WaiterConfig={'Delay':30, 'MaxAttempts':120})
        db = ops.owned_database(session, app)
    # Never attach a database from a different network, even on a retry.
    if db.get('DBSubnetGroup', {}).get('VpcId') != network['vpcId']:
        raise click.ClickException('Managed database and application cluster must use the same VPC.')
    if db.get('PubliclyAccessible') is not False or db.get('StorageEncrypted') is not True:
        raise click.ClickException('Managed PostgreSQL must be private and encrypted.')
    group_ids = [item['VpcSecurityGroupId'] for item in db.get('VpcSecurityGroups', [])]
    if len(group_ids) != 1:
        raise click.ClickException('The owned database security group is unavailable.')
    ec2 = session.client('ec2')
    groups = ec2.describe_security_groups(GroupIds=group_ids).get('SecurityGroups', [])
    if len(groups) != 1 or groups[0].get('VpcId') != network['vpcId'] or {item['Key']:item['Value'] for item in groups[0].get('Tags', [])}.get('ebkit:application') != app:
        raise click.ClickException('Database network ownership could not be verified.')
    try:
        ec2.authorize_security_group_ingress(GroupId=group_ids[0], IpPermissions=[{
            'IpProtocol':'tcp', 'FromPort':5432, 'ToPort':5432,
            'UserIdGroupPairs':[{'GroupId':network['clusterSecurityGroupId']}]}])
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') != 'InvalidPermission.Duplicate':
            raise
    endpoint = db.get('Endpoint', {})
    arn = db.get('MasterUserSecret', {}).get('SecretArn')
    if db.get('DBInstanceStatus') != 'available' or not arn or not endpoint.get('Address'):
        raise click.ClickException('Database or managed credentials are not ready. Retry after checking database status.')
    credentials = json.loads(session.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString'])
    url = 'postgresql://' + quote(credentials['username'], safe='') + ':' + quote(credentials['password'], safe='') + '@' + endpoint['Address'] + ':' + str(endpoint['Port']) + '/ebkit?sslmode=require'
    record.update(database_id=db['DBInstanceIdentifier'], database_variables=plan['variables'])
    ops.save_deployment(record)
    click.echo('PostgreSQL ready. Connection settings will be supplied to the application; values are hidden.')
    return {name:url for name in plan['variables']}, subnets
