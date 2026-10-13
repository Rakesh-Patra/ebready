import importlib
import json
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner
from ebkit.deployment_state import save_deployment, load_deployments
from ebkit.cli import cli

module=importlib.import_module('ebkit.commands.watch')
A='a'*40
B='b'*40
C='c'*40

@pytest.fixture
def setup(monkeypatch,tmp_path):
    monkeypatch.setenv('EBKIT_STATE_FILE',str(tmp_path/'state.json'))
    target=dict(account_id='123',region='us-east-2',application_name='custom-app',environment_name='custom-env',
                source_url='https://github.com/owner/repo',port=5000,url='https://stable.example',status='Ready',source_commit=A)
    save_deployment(target)
    monkeypatch.setattr(module,'_check_aws_credentials',lambda:(True,'123',None))
    eb=MagicMock();eb.describe_environments.return_value={'Environments':[{'Status':'Ready'}]}
    session=MagicMock();session.client.return_value=eb
    monkeypatch.setattr(module.boto3,'Session',lambda **kwargs:session)
    deploy=MagicMock()
    monkeypatch.setattr(module,'_execute_deploy',deploy)
    monkeypatch.setattr(module,'github_head',lambda source:B)
    return target,eb,deploy


def test_registered_watch_deploys_new_commit_with_exact_saved_target(setup):
    target,eb,deploy=setup
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code==0,result.output
    assert deploy.call_args.args== (target['source_url'],'custom-app','custom-env','us-east-2',5000,True,None,(),False)
    assert deploy.call_args.kwargs==dict(commit=B,require_existing=True)
    record=load_deployments()[0]
    assert record['watch_commit']==B and record['url']==target['url']
    assert 'New commit' in result.output


def test_unchanged_commit_does_not_build(setup,monkeypatch):
    target,eb,deploy=setup
    monkeypatch.setattr(module,'github_head',lambda source:A)
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code==0
    deploy.assert_not_called()


def test_legacy_deployment_baselines_without_build(setup):
    target,eb,deploy=setup;target.pop('source_commit');save_deployment(target)
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code==0 and 'Baseline' in result.output
    deploy.assert_not_called()


def test_failed_commit_is_not_retried_after_restart(setup):
    target,eb,deploy=setup
    deploy.side_effect=RuntimeError('Build failed')
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code!=0
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code==0
    assert deploy.call_count==1


def test_waits_while_busy_and_builds_latest_commit_afterward(setup,monkeypatch):
    target,eb,deploy=setup
    eb.describe_environments.side_effect=[{'Environments':[{'Status':'Updating'}]},{'Environments':[{'Status':'Ready'}]}]
    head=MagicMock(return_value=C);monkeypatch.setattr(module,'github_head',head)
    count=0
    def sleep(interval):
        nonlocal count
        count+=1
        if count==2:raise KeyboardInterrupt
    monkeypatch.setattr(module.time,'sleep',sleep)
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--yes'])
    assert result.exit_code==0 and 'Watch stopped' in result.output
    head.assert_called_once()
    assert deploy.call_args.kwargs['commit']==C


@pytest.mark.parametrize('environments',[[],[{'Status':'Terminating'}]])
def test_missing_environment_stops_without_recreation(setup,environments):
    target,eb,deploy=setup;eb.describe_environments.return_value={'Environments':environments}
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code!=0 and 'No replacement' in result.output
    deploy.assert_not_called()


def test_github_error_does_not_mark_commit_or_start_build(setup,monkeypatch):
    target,eb,deploy=setup
    def fail(source):raise ValueError('GitHub unavailable')
    monkeypatch.setattr(module,'github_head',fail)
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once','--yes'])
    assert result.exit_code!=0
    assert 'watch_commit' not in load_deployments()[0]
    deploy.assert_not_called()


@pytest.mark.parametrize('source',['project.zip','http://github.com/a/b','https://github.com/a/b?token=bad','https://evil.com/a/b'])
def test_invalid_sources_rejected_before_aws(source,monkeypatch):
    aws=MagicMock();monkeypatch.setattr(module,'_check_aws_credentials',aws)
    result=CliRunner().invoke(cli,['watch',source,'--yes'])
    assert result.exit_code!=0
    aws.assert_not_called()


def test_ambiguous_or_wrong_account_targets_rejected(setup):
    target,eb,deploy=setup
    other={**target,'environment_name':'other-env'};save_deployment(other)
    with pytest.raises(click.ClickException):module.select_target(target['source_url'],'123')
    with pytest.raises(click.ClickException):module.select_target(target['source_url'],'other-account')
    assert module.select_target(target['source_url'],'123',environment='custom-env')['environment_name']=='custom-env'


def test_startup_confirmation_can_decline_without_build(setup):
    target,eb,deploy=setup
    result=CliRunner().invoke(cli,['watch',target['source_url'],'--once'],input='n\n')
    assert result.exit_code!=0
    deploy.assert_not_called()


def test_duplicate_watcher_is_rejected_and_lock_is_released(setup):
    target,eb,deploy=setup
    with module.watch_lock(target):
        with pytest.raises(click.ClickException,match='Another watcher'):
            with module.watch_lock(target):pass
    with module.watch_lock(target):pass


def test_github_head_validates_commit_and_root_dockerfile(monkeypatch):
    responses=iter([{'private':False,'default_branch':'release/main'},{'sha':B},{'type':'file','size':200}])
    urls=[]
    def opened(request,timeout):
        urls.append(request.full_url)
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps(next(responses)).encode()
        return response
    monkeypatch.setattr(module,'urlopen',opened)
    assert module.github_head('https://github.com/owner/repo')==B
    assert '/commits/release%2Fmain' in urls[1]
    assert urls[2].endswith('/contents/Dockerfile?ref='+B)


@pytest.mark.parametrize('sha',['main','bad','0'*39])
def test_github_rejects_invalid_commit(monkeypatch,sha):
    values=iter([{'private':False,'default_branch':'main'},{'sha':sha}])
    def opened(*args,**kwargs):
        response=MagicMock();response.__enter__.return_value=response;response.read.return_value=json.dumps(next(values)).encode();return response
    monkeypatch.setattr(module,'urlopen',opened)
    with pytest.raises(ValueError,match='invalid commit'):module.github_head('https://github.com/a/b')
