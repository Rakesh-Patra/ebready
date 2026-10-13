import importlib
import json
import os
from unittest.mock import MagicMock
import click
import pytest
from click.testing import CliRunner
from ebkit.commands import manage
from ebkit.deployment_state import load_deployments
from ebkit.cli import cli
controls=importlib.import_module('ebkit.commands.controls')

@pytest.fixture
def setup(monkeypatch,tmp_path):
 monkeypatch.setenv('EBKIT_STATE_FILE',str(tmp_path/'state.json'))
 eb=MagicMock();session=MagicMock();session.region_name='us-east-2';session.client.side_effect=lambda name:eb if name=='elasticbeanstalk' else MagicMock(get_caller_identity=lambda:{'Account':'123'})
 source=dict(EnvironmentName='source',EnvironmentId='e-src',Status='Ready',Tier={'Name':'Cluster','Type':'EKS'},VersionLabel='v1',CNAME='source.example')
 def environments(**args):
  names=args.get('EnvironmentNames',['source'])
  return {'Environments':[dict(source,EnvironmentName=name,CNAME=name+'.example') for name in names if name!='new-env']}
 eb.describe_environments.side_effect=environments
 eb.describe_configuration_settings.return_value={'ConfigurationSettings':[{'OptionSettings':[{'Namespace':manage.ENV_NAMESPACE,'OptionName':'env-variables','Value':'SECRET'}]}]}
 record=dict(account_id='123',region='us-east-2',application_name='app',environment_name='source',source_url='https://github.com/a/b',port=5000)
 monkeypatch.setattr(manage,'target',lambda *args:(session,'app','source',record))
 return eb,session,source


def invoke(command,args=()):return CliRunner().invoke(command,list(args))


def test_events_follow_deduplicates_and_requests_pagination(setup,monkeypatch):
 eb,_,_=setup
 row=dict(EventDate='2026-10-13',RequestId='one',Message='Deploy started',Severity='INFO')
 eb.describe_events.side_effect=[{'Events':[row],'NextToken':'next'},{'Events':[]},{'Events':[row]}]
 calls=0
 def sleep(_):
  nonlocal calls
  calls+=1
  if calls==2:raise KeyboardInterrupt
 monkeypatch.setattr(controls.time,'sleep',sleep)
 result=invoke(controls.events_command,['--follow'])
 assert result.exit_code==0 and result.output.count('Deploy started')==1
 assert eb.describe_events.call_args_list[1].kwargs['NextToken']=='next'
 assert eb.describe_events.call_args_list[2].kwargs['StartTime']=='2026-10-13'


def test_health_reports_causes_and_metrics_without_instance_api(setup):
 eb,_,_=setup
 eb.describe_environment_health.return_value={'HealthStatus':'Degraded','Color':'Yellow','Causes':['High error rate'],'ApplicationMetrics':{'RequestCount':10}}
 result=invoke(controls.health_command,['--json'])
 assert result.exit_code==0 and json.loads(result.output)['Causes']==['High error rate']
 eb.describe_environment_health.assert_called_once_with(EnvironmentName='source',AttributeNames=['All'])
 eb.describe_instances_health.assert_not_called()


def test_options_read_hides_values_and_write_uses_file(setup,tmp_path):
 eb,_,_=setup
 result=invoke(controls.options_command,['--json'])
 assert result.exit_code==0 and 'SECRET' not in result.output
 file=tmp_path/'options.json';rows=[dict(Namespace='aws:elasticbeanstalk:eks:environment:startup-probe',OptionName='timeout-seconds',Value='5')];file.write_text(json.dumps(rows))
 result=invoke(controls.options_command,['--file',str(file),'--yes'])
 assert result.exit_code==0,result.output
 assert eb.update_environment.call_args.kwargs['OptionSettings']==rows


@pytest.mark.parametrize('rows',[[dict(Namespace=manage.ENV_NAMESPACE,OptionName='env-variables',Value='secret')],[dict(Namespace=manage.ENV_NAMESPACE,OptionName='service-port',Value='5000')],[dict(Namespace='x',OptionName='y',Value='secret')]*2])
def test_options_reject_managed_and_duplicate_settings(setup,tmp_path,rows):
 eb,_,_=setup;file=tmp_path/'bad.json';file.write_text(json.dumps(rows))
 result=invoke(controls.options_command,['--file',str(file),'--yes'])
 assert result.exit_code!=0 and 'secret' not in result.output
 eb.update_environment.assert_not_called()


def test_saved_config_is_blocked_for_cluster_and_supported_for_standard(setup):
 eb,_,source=setup
 for command in [controls.config_save_command,controls.config_apply_command]:
  result=invoke(command,['--name','saved','--yes']);assert result.exit_code!=0 and 'Standard Mode only' in result.output
 eb.create_configuration_template.assert_not_called();eb.update_environment.assert_not_called()
 source['Tier']={'Name':'WebServer','Type':'Standard'}
 assert invoke(controls.config_save_command,['--name','saved','--yes']).exit_code==0
 eb.create_configuration_template.assert_called_once_with(ApplicationName='app',TemplateName='saved',EnvironmentId='e-src')
 assert invoke(controls.config_apply_command,['--name','saved','--yes']).exit_code==0
 assert eb.update_environment.call_args.kwargs['TemplateName']=='saved'


def test_version_delete_refuses_versions_used_on_later_page(setup):
 eb,_,_=setup;eb.describe_environments.side_effect=[{'Environments':[],'NextToken':'next'},{'Environments':[{'VersionLabel':'old'}]}]
 result=invoke(controls.version_delete_command,['--version','old','--yes'])
 assert result.exit_code!=0 and 'active environment' in result.output
 eb.delete_application_version.assert_not_called()


def test_version_delete_preserves_source_bundle_and_requires_confirmation(setup):
 eb,_,_=setup;eb.describe_application_versions.return_value={'ApplicationVersions':[{'VersionLabel':'old'}]}
 result=invoke(controls.version_delete_command,['--version','old'],)
 assert result.exit_code!=0;eb.delete_application_version.assert_not_called()
 assert invoke(controls.version_delete_command,['--version','old','--yes']).exit_code==0
 eb.delete_application_version.assert_called_once_with(ApplicationName='app',VersionLabel='old',DeleteSourceBundle=False)


def test_swap_validates_both_targets_and_calls_url_swap(setup):
 eb,_,_=setup
 result=invoke(controls.swap_command,['--destination','green','--yes'])
 assert result.exit_code==0,result.output
 eb.swap_environment_cnames.assert_called_once_with(SourceEnvironmentName='source',DestinationEnvironmentName='green')
 assert all(call.kwargs['ApplicationName']=='app' for call in eb.describe_environments.call_args_list)


def test_swap_rejects_non_ready_destination(setup):
 eb,_,_=setup;eb.describe_environments.side_effect=[{'Environments':[{'Status':'Ready','CNAME':'a','Tier':{'Type':'EKS'}}]},{'Environments':[{'Status':'Updating','CNAME':'b','Tier':{'Type':'EKS'}}]}]
 assert invoke(controls.swap_command,['--destination','green','--yes']).exit_code!=0
 eb.swap_environment_cnames.assert_not_called()


def test_clone_copies_version_settings_and_records_new_environment(setup,monkeypatch):
 eb,_,_=setup;eb.create_environment.return_value={'Status':'Launching'}
 monkeypatch.setattr(controls,'_poll_environment_health',lambda *args:{'status':'Ready','health':'Green','url':'https://new.example'})
 result=invoke(controls.clone_command,['--name','new-env','--max-replicas','1','--yes'])
 assert result.exit_code==0,result.output
 args=eb.create_environment.call_args.kwargs
 assert args['EnvironmentName']=='new-env' and args['VersionLabel']=='v1'
 assert args['Tier']=={'Name':'Cluster','Type':'EKS'}
 assert next(item['Value'] for item in args['OptionSettings'] if item['OptionName']=='max-replica')=='1'
 assert 'SECRET' not in result.output
 record=load_deployments()[0];assert record['environment_name']=='new-env' and record['port']==5000 and record['url']=='https://new.example'


def test_clone_rejects_existing_destination_before_creation(setup):
 eb,_,_=setup
 result=invoke(controls.clone_command,['--name','source','--yes'])
 assert result.exit_code!=0
 eb.create_environment.assert_not_called()


def test_profile_is_scoped_to_one_cli_invocation(monkeypatch):
 monkeypatch.setenv('AWS_PROFILE','original')
 @click.command('profile-probe')
 def probe():click.echo(os.environ.get('AWS_PROFILE'))
 cli.add_command(probe)
 try:
  result=CliRunner().invoke(cli,['--profile','selected','profile-probe'])
  assert result.exit_code==0 and 'selected' in result.output
  assert os.environ['AWS_PROFILE']=='original'
 finally:cli.commands.pop('profile-probe')


def test_worker_profile_cannot_switch_to_another_aws_account(monkeypatch):
    from ebkit.commands import operations, deploy as deploy_module
    monkeypatch.setenv("EBKIT_EXPECTED_AWS_ACCOUNT","123456789012")
    session=MagicMock();session.client.return_value.get_caller_identity.return_value={"Account":"999999999999"}
    monkeypatch.setattr(operations.boto3,"Session",lambda **kwargs:session)
    with pytest.raises(click.ClickException,match="does not match"):
        operations._aws_session("us-east-2")
    assert deploy_module._check_aws_credentials()[0] is False


def test_profile_clears_cached_sdk_session_and_restores_it(monkeypatch):
    import boto3
    original=object();monkeypatch.setattr(boto3,"DEFAULT_SESSION",original)
    @click.command("profile-cache-probe")
    def probe():
        assert boto3.DEFAULT_SESSION is None
    cli.add_command(probe)
    try:
        result=CliRunner().invoke(cli,["--profile","selected","profile-cache-probe"])
        assert result.exit_code==0
        assert boto3.DEFAULT_SESSION is original
    finally:cli.commands.pop("profile-cache-probe")


def test_options_accept_path_names_and_reject_control_characters():
    assert controls.option_key("aws:elasticbeanstalk:environment:proxy:staticfiles//static/")["OptionName"]=="/static/"
    with pytest.raises(click.UsageError):controls.option_key("namespace/name\ninvalid")
