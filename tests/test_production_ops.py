import json
from datetime import datetime,timezone
from unittest.mock import MagicMock
from botocore.exceptions import ClientError
import pytest
from ebkit.commands import production_ops as ops
from ebkit.pipeline import generate_pipeline
from ebkit.release import validate_policy,buildspec,verify_or_recover


def freeze_cost_date(monkeypatch):
 clock=MagicMock(wraps=datetime);clock.now.return_value=datetime(2026,10,14,tzinfo=timezone.utc)
 monkeypatch.setattr(ops,'datetime',clock)


def test_release_policy_validates_test_arguments_and_health_path():
 policy=validate_policy({'testCommands':[['npm','ci'],['npm','test']],'healthPath':'/health','autoRollback':True})
 spec=buildspec(policy);assert spec.index('npm test')<spec.index('docker build')
 with pytest.raises(ValueError):validate_policy({'healthPath':'//evil.com'})
 with pytest.raises(ValueError):validate_policy({'testCommands':['npm test']})


def test_release_recovers_previous_version_without_changing_settings(monkeypatch):
 eb=MagicMock();eb.describe_environments.side_effect=[{'Environments':[{'Status':'Ready','Health':'Red','VersionLabel':'new'}]},{'Environments':[{'Status':'Ready','Health':'Green','VersionLabel':'old'}]}]
 result=verify_or_recover(eb,'app','env','new','old',validate_policy({'autoRollback':True}),lambda *args:{'status':'Ready','health':'Green'})
 assert result['recovery']=='restored';eb.update_environment.assert_called_once_with(EnvironmentName='env',VersionLabel='old')


def test_release_does_not_overwrite_a_concurrent_newer_release():
 eb=MagicMock();eb.describe_environments.return_value={'Environments':[{'VersionLabel':'newer','Status':'Ready','Health':'Green'}]}
 with pytest.raises(RuntimeError):verify_or_recover(eb,'app','env','new','old',validate_policy({'autoRollback':True}),lambda *args:{})
 eb.update_environment.assert_not_called()


def test_pipeline_gates_deploy_on_tests_and_uses_oidc():
 bundle=generate_pipeline('app','env','us-east-2','https://github.com/owner/repo',{'roleArn':'arn:aws:iam::123456789012:role/deploy'})
 assert 'id-token: write' in bundle['workflow'] and 'needs:' in bundle['workflow'] and 'environment: production' in bundle['workflow']
 assert 'AWS_ACCESS_KEY_ID' not in bundle['workflow']
 assert bundle['oidcTrustPolicy']['Statement'][0]['Condition']['StringEquals']['token.actions.githubusercontent.com:sub']=='repo:owner/repo:environment:production'


def test_autoscaling_uses_actual_cluster_trigger_names(monkeypatch):
 eb=MagicMock();monkeypatch.setattr(ops.manage,'cluster_only',lambda *args:None);eb.describe_events.return_value={'Events':[]}
 ops.autoscale(eb,'app','env',{'minimum':1,'maximum':4,'cpuPercent':75,'memoryPercent':80})
 settings={row['OptionName']:row['Value'] for row in eb.update_environment.call_args.kwargs['OptionSettings']}
 assert settings['cpu-metric-type']=='Utilization' and settings['cpu-value']=='75'
 assert settings['memory-value']=='80' and settings['max-replica']=='4'


def test_cost_no_data_is_unknown_not_zero(monkeypatch):
 freeze_cost_date(monkeypatch)
 session=MagicMock();client=session.client.return_value;client.get_cost_and_usage.return_value={'ResultsByTime':[]}
 client.get_cost_forecast.side_effect=ClientError({'Error':{'Code':'DataUnavailableException','Message':'Not enough data'}},'GetCostForecast')
 report=ops.cost_report(session,'env',{});assert report['taggedCostUsd'] is None and report['monthlyRunRateUsd'] is None
 assert report['remainingMonthForecastUsd'] is None and report['forecastStatus']=='unavailable'
 assert 'shared' in report['coverage']


def test_cost_report_includes_tagged_aws_forecast(monkeypatch):
 freeze_cost_date(monkeypatch)
 session=MagicMock();client=session.client.return_value
 client.get_cost_and_usage.return_value={'ResultsByTime':[{'Groups':[{'Metrics':{'UnblendedCost':{'Amount':'3.50'}}}]}]}
 client.get_cost_forecast.return_value={'Total':{'Amount':'12.34','Unit':'USD'}}
 report=ops.cost_report(session,'env',{'days':7})
 assert report['remainingMonthForecastUsd']==12.34 and report['forecastStatus']=='available'
 assert report['forecastPeriod']['end']>report['forecastPeriod']['start']
 assert client.get_cost_forecast.call_args.kwargs['TimePeriod']=={'Start':'2026-10-15','End':'2026-11-01'}
 assert client.get_cost_forecast.call_args.kwargs['Filter']=={'Tags':{'Key':'elasticbeanstalk:environment-name','Values':['env']}}


def test_cost_report_surfaces_forecast_permission_errors(monkeypatch):
 freeze_cost_date(monkeypatch)
 session=MagicMock();client=session.client.return_value;client.get_cost_and_usage.return_value={'ResultsByTime':[]}
 client.get_cost_forecast.side_effect=ClientError({'Error':{'Code':'AccessDeniedException','Message':'Denied'}},'GetCostForecast')
 with pytest.raises(ClientError):ops.cost_report(session,'env',{})


def test_database_creation_is_private_encrypted_and_uses_managed_password(monkeypatch):
 ec2=MagicMock();rds=MagicMock();session=MagicMock();session.client.side_effect=lambda name:ec2 if name=='ec2' else rds
 ec2.describe_subnets.return_value={'Subnets':[{'VpcId':'vpc-abc','AvailabilityZone':'a'},{'VpcId':'vpc-abc','AvailabilityZone':'b'}]}
 ec2.describe_security_groups.return_value={'SecurityGroups':[{'VpcId':'vpc-abc'}]};ec2.create_security_group.return_value={'GroupId':'sg-def'}
 monkeypatch.setattr(ops,'save_deployment',lambda record:None)
 ops.database_create(session,MagicMock(),'app','env',{}, {'vpcId':'vpc-abc','subnetIds':['subnet-a','subnet-b'],'applicationSecurityGroup':'sg-abc'})
 args=rds.create_db_instance.call_args.kwargs
 assert args['PubliclyAccessible'] is False and args['StorageEncrypted'] is True and args['ManageMasterUserPassword'] is True and args['DeletionProtection'] is True
 assert 'MasterUserPassword' not in args
 rule=ec2.authorize_security_group_ingress.call_args.kwargs['IpPermissions'][0];assert 'IpRanges' not in rule


def test_database_rejects_wrong_vpc_before_creation():
 session=MagicMock();session.client.return_value.describe_subnets.return_value={'Subnets':[{'VpcId':'vpc-other','AvailabilityZone':'a'}]}
 with pytest.raises(Exception):ops.database_create(session,MagicMock(),'app','env',{}, {'vpcId':'vpc-abc','subnetIds':['a','b'],'applicationSecurityGroup':'sg-abc'})
 session.client.return_value.create_db_instance.assert_not_called()
