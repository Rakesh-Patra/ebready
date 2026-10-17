import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

spec = importlib.util.spec_from_file_location('dashboard_release', Path(__file__).parents[1] / 'scripts/release_dashboard.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def client(supported=True, status='Processed'):
    eb = MagicMock()
    eb.meta.service_model.operation_model.return_value.input_shape.members = {'ImageConfiguration': {}} if supported else {}
    eb.create_application_version.return_value = {'ApplicationVersion': {'Status': status}}
    return eb


def test_cluster_version_registers_immutable_image_without_standard_bundle():
    eb = client()
    release.create_cluster_version(eb, 'app', 'release', 'image@sha256:digest')
    assert eb.create_application_version.call_args.kwargs == {
        'ApplicationName': 'app', 'VersionLabel': 'release',
        'ImageConfiguration': {'Source': {'Uri': 'image@sha256:digest'}}}


@pytest.mark.parametrize('status', ['Failed', 'FAILED'])
def test_rejected_cluster_image_stops_delivery(status):
    with pytest.raises(SystemExit, match='environment was not changed'):
        release.create_cluster_version(client(status=status), 'app', 'release', 'image')


def test_old_sdk_uses_cli_without_shell_or_credentials_in_arguments():
    result = SimpleNamespace(returncode=0, stdout=json.dumps({'ApplicationVersion': {'Status': 'Processed'}}))
    with patch('subprocess.run', return_value=result) as run:
        release.create_cluster_version(client(False), 'app', 'release', 'image')
    args, kwargs = run.call_args
    assert args[0][:3] == ['aws', 'elasticbeanstalk', 'create-application-version']
    assert 'shell' not in kwargs
    assert json.loads(args[0][4])['ImageConfiguration']['Source']['Uri'] == 'image'


def test_cli_failure_does_not_continue_to_deployment():
    with patch('subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='Denied')):
        with pytest.raises(SystemExit, match='registration failed'):
            release.create_cluster_version(client(False), 'app', 'release', 'image')


def test_ci_changes_only_stack_version_and_waits_for_completion():
    cfn = MagicMock()
    release.update_cluster_stack(cfn, 'stack', 'release')
    assert cfn.update_stack.call_args.kwargs == {'StackName': 'stack', 'UsePreviousTemplate': True,
        'Parameters': [{'ParameterKey': 'VersionLabel', 'ParameterValue': 'release'}]}
    cfn.get_waiter.assert_called_once_with('stack_update_complete')
    cfn.get_waiter.return_value.wait.assert_called_once()
