import json
from unittest.mock import MagicMock

import click
import pytest
from botocore.exceptions import ClientError

from ebkit.managed_database import prepare_database, validate_plan
from ebkit.commands import production_ops as ops

PLAN={'clusterName':'eb-existing','subnetIds':['subnet-aaa','subnet-bbb'],'variables':['POSTGRES_URL']}

@pytest.mark.parametrize('changes',[
    {'variables':[['bad']]}, {'variables':['MYSQL_URL']}, {'variables':['POSTGRES_URL','POSTGRES_URL']},
    {'storageGb':20.5}, {'storageGb':True}, {'multiAz':'false'}, {'instanceClass':'invalid'},
    {'subnetIds':['subnet-aaa','subnet-aaa']}, {'public':True},
])
def test_bad_plans_rejected_before_resources(changes):
    with pytest.raises(click.ClickException):validate_plan({**PLAN,**changes})

def fixture(monkeypatch):
    clients={name:MagicMock() for name in ['eks','rds','ec2','secretsmanager','elasticbeanstalk']}
    session=MagicMock();session.client.side_effect=lambda name:clients[name]
    clients['eks'].describe_cluster.return_value={'cluster':{'status':'ACTIVE','resourcesVpcConfig':{'vpcId':'vpc-abc','subnetIds':['subnet-ccc','subnet-ddd'],'clusterSecurityGroupId':'sg-abc'}}}
    clients['ec2'].describe_security_groups.return_value={'SecurityGroups':[{'VpcId':'vpc-abc','Tags':[{'Key':'ebkit:application','Value':'app'}]}]}
    clients['secretsmanager'].get_secret_value.return_value={'SecretString':json.dumps({'username':'user','password':'secret/@?'})}
    saved=[];monkeypatch.setattr(ops,'save_deployment',lambda record:saved.append(dict(record)))
    db={'DBInstanceIdentifier':ops.db_id('app'),'DBInstanceStatus':'available','PubliclyAccessible':False,'StorageEncrypted':True,'VpcSecurityGroups':[{'VpcSecurityGroupId':'sg-def'}],'DBSubnetGroup':{'VpcId':'vpc-abc'},'Endpoint':{'Address':'private.example','Port':5432},'MasterUserSecret':{'SecretArn':'arn:secret'}}
    return session,clients,db,saved

def test_creates_waits_then_supplies_requested_variable_without_persisting_password(monkeypatch,capsys):
    session,clients,db,saved=fixture(monkeypatch)
    missing=ClientError({'Error':{'Code':'DBInstanceNotFound'}},'DescribeDBInstances')
    owned=MagicMock(side_effect=[missing,db]);monkeypatch.setattr(ops,'owned_database',owned)
    create=MagicMock();monkeypatch.setattr(ops,'database_create',create)
    values,subnets=prepare_database(session,'app','env',{'application_name':'app'},PLAN)
    create.assert_called_once();clients['rds'].get_waiter.return_value.wait.assert_called_once()
    assert set(values)=={'POSTGRES_URL'} and 'secret%2F%40%3F' in values['POSTGRES_URL']
    assert subnets==['subnet-ccc','subnet-ddd']
    assert 'secret/' not in json.dumps(saved)+capsys.readouterr().out
    assert 'postgresql://' not in json.dumps(saved)

def test_retry_reuses_owned_available_database(monkeypatch):
    session,clients,db,saved=fixture(monkeypatch)
    monkeypatch.setattr(ops,'owned_database',lambda *args:db)
    create=MagicMock();monkeypatch.setattr(ops,'database_create',create)
    values,_=prepare_database(session,'app','env',{},PLAN)
    assert values['POSTGRES_URL'].startswith('postgresql://')
    create.assert_not_called();clients['rds'].get_waiter.assert_not_called()

def test_wrong_vpc_never_fetches_credentials(monkeypatch):
    session,clients,db,_=fixture(monkeypatch);db['DBSubnetGroup']['VpcId']='vpc-other'
    monkeypatch.setattr(ops,'owned_database',lambda *args:db)
    with pytest.raises(click.ClickException,match='same VPC'):prepare_database(session,'app','env',{},PLAN)
    clients['secretsmanager'].get_secret_value.assert_not_called()

def test_invalid_database_sizing_creates_no_network_resources(monkeypatch):
    session,clients,_,_=fixture(monkeypatch)
    clients['ec2'].describe_subnets.return_value={'Subnets':[{'VpcId':'vpc-abc','AvailabilityZone':'a'},{'VpcId':'vpc-abc','AvailabilityZone':'b'}]}
    clients['ec2'].describe_security_groups.return_value={'SecurityGroups':[{'VpcId':'vpc-abc'}]}
    with pytest.raises(click.UsageError):ops.database_create(session,clients['elasticbeanstalk'],'app','env',{}, {'vpcId':'vpc-abc','subnetIds':['subnet-aaa','subnet-bbb'],'applicationSecurityGroup':'sg-abc','instanceClass':'invalid'})
    clients['ec2'].create_security_group.assert_not_called();clients['rds'].create_db_subnet_group.assert_not_called()

def test_attachment_uses_the_app_variable_and_preserves_other_settings(monkeypatch):
    session,clients,db,_=fixture(monkeypatch)
    monkeypatch.setattr(ops,'owned_database',lambda *args:db)
    monkeypatch.setattr(ops.manage,'settings',lambda *args:[{'Namespace':ops.manage.ENV_NAMESPACE,'OptionName':'env-variables','Value':'{"OTHER":"keep"}'}])
    result=ops.database_action('database-attach',session,clients['elasticbeanstalk'],'app','env',{}, {'variable':'POSTGRES_URL'})
    variables=json.loads(clients['elasticbeanstalk'].update_environment.call_args.kwargs['OptionSettings'][0]['Value'])
    assert variables['OTHER']=='keep' and 'POSTGRES_URL' in variables and 'DATABASE_URL' not in variables
    assert 'secret' not in json.dumps(result)

def test_foreign_security_group_never_receives_a_network_grant(monkeypatch):
    session,clients,db,_=fixture(monkeypatch)
    monkeypatch.setattr(ops,'owned_database',lambda *args:db)
    clients['ec2'].describe_security_groups.return_value={'SecurityGroups':[{'VpcId':'vpc-abc','Tags':[]}]}
    with pytest.raises(click.ClickException,match='ownership'):prepare_database(session,'app','env',{},PLAN)
    clients['ec2'].authorize_security_group_ingress.assert_not_called()
    clients['secretsmanager'].get_secret_value.assert_not_called()
