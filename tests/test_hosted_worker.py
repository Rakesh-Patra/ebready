import json
from unittest.mock import MagicMock, patch
import pytest
from ebkit.commands.deploy import _ensure_iam_role, _provision_cluster_infrastructure, _build_cluster_option_settings


def test_hosted_cluster_roles_never_create_or_modify_shared_roles(monkeypatch):
    roles={key:'arn:aws:iam::123456789012:role/'+key for key in ['cluster_role','node_role','observability_role']}
    monkeypatch.setenv('EBKIT_PREPROVISIONED_ROLES',json.dumps(roles))
    client=MagicMock();client.describe_vpcs.return_value={'Vpcs':[]}
    with patch('ebkit.commands.deploy.boto3.client',return_value=client):
        assert _provision_cluster_infrastructure('123456789012','us-east-2')=={**roles,'subnets':[]}
    client.create_role.assert_not_called();client.attach_role_policy.assert_not_called();client.create_service_linked_role.assert_not_called()


def test_hosted_build_role_refuses_missing_boundary(monkeypatch):
    monkeypatch.setenv('EBKIT_BUILD_PERMISSIONS_BOUNDARY','arn:aws:iam::123456789012:policy/build-boundary')
    iam=MagicMock();iam.get_role.return_value={'Role':{'Arn':'role'}}
    with pytest.raises(Exception,match='lacks the hosted permissions boundary'):
        _ensure_iam_role(iam,'build','codebuild.amazonaws.com',[])
    iam.put_role_policy.assert_not_called()


def test_cluster_options_do_not_pass_standard_or_removed_operation_roles():
    options=_build_cluster_option_settings({'cluster_role':'cluster','node_role':'node','observability_role':'logs'}, {},8080,1)
    assert not any(item['OptionName']=='operation-role' for item in options)
    assert all(item['Namespace'].startswith('aws:elasticbeanstalk:eks') for item in options)


def test_hosted_metadata_survives_local_file_loss(monkeypatch, tmp_path):
    from ebkit.deployment_state import load_deployments
    import io
    monkeypatch.setenv('EBKIT_STATE_S3_BUCKET','private-state')
    s3=MagicMock();s3.list_objects_v2.return_value={'Contents':[{'Key':'worker-state/11111111-1111-4111-8111-111111111111.json'}]};s3.get_object.return_value={'Body':io.BytesIO(json.dumps({'deployments':[{'environment_name':'ebk-example-cluster'}]}).encode())}
    with patch('boto3.client',return_value=s3):
        records=load_deployments(tmp_path/'11111111-1111-4111-8111-111111111111.json')
    assert records[0]['environment_name']=='ebk-example-cluster'
    assert s3.get_object.call_args.kwargs['Key'].startswith('worker-state/')


def test_hosted_metadata_fails_closed_when_remote_storage_is_denied(monkeypatch, tmp_path):
    from ebkit.deployment_state import load_deployments
    from botocore.exceptions import ClientError
    monkeypatch.setenv('EBKIT_STATE_S3_BUCKET','private-state');s3=MagicMock();s3.list_objects_v2.return_value={'Contents':[{'Key':'worker-state/11111111-1111-4111-8111-111111111111.json'}]}
    s3.get_object.side_effect=ClientError({'Error':{'Code':'AccessDenied'}},'GetObject')
    with patch('boto3.client',return_value=s3), pytest.raises(ClientError):
        load_deployments(tmp_path/'11111111-1111-4111-8111-111111111111.json')


def test_idle_hosted_environment_receives_verified_https_probe(monkeypatch):
    from ebkit.commands.deploy import _poll_environment_health
    monkeypatch.setenv('EBKIT_HOSTED_REGION','us-east-2')
    client=MagicMock();host='sample.us-east-2.elasticbeanstalk.com'
    client.describe_environments.side_effect=[{'Environments':[{'Status':'Ready','Health':health,'CNAME':host}]} for health in ['Grey','Green']]
    with patch('urllib.request.urlopen') as request, patch('ebkit.commands.deploy.time.sleep'):
        result=_poll_environment_health(client,'app','environment')
    request.assert_called_once_with('https://'+host+'/',timeout=5)
    assert result['health']=='Green'
