import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from ebkit.commands import manage
from ebkit.cli import cli


@pytest.fixture
def target(monkeypatch):
    eb = MagicMock()
    session = MagicMock()
    session.client.return_value = eb
    eb.describe_environments.return_value = {"Environments": [{"Tier": {"Type": "EKS"}, "Status": "Ready"}]}
    eb.describe_configuration_settings.return_value = {"ConfigurationSettings": [{"OptionSettings": [{
        "Namespace": manage.ENV_NAMESPACE, "OptionName": "env-variables",
        "Value": json.dumps({"PORT": "8080", "DATABASE_URL": "hidden-existing-secret", "KEEP": "yes"}),
    }]}]}
    monkeypatch.setattr(manage, "target", lambda *args: (session, "demo", "demo-cluster", {}))
    return eb


def test_config_merges_file_and_flags_without_printing_values(target, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=8080\nNEW=file-secret\n")
    result = CliRunner().invoke(manage.config_command, ["--env-file", str(env_file), "--env", "NEW=override-secret", "--unset", "KEEP", "--yes"])
    assert result.exit_code == 0, result.output
    value = json.loads(target.update_environment.call_args.kwargs["OptionSettings"][0]["Value"])
    assert value == {"PORT": "8080", "DATABASE_URL": "hidden-existing-secret", "NEW": "override-secret"}
    assert "secret" not in result.output


def test_config_list_hides_every_value(target):
    result = CliRunner().invoke(manage.config_command)
    assert result.exit_code == 0
    assert "DATABASE_URL" in result.output
    assert "hidden-existing-secret" not in result.output
    target.update_environment.assert_not_called()


@pytest.mark.parametrize("flags", [["--env", "PORT=9090"], ["--env", "KEY="], ["--env", "KEY=value", "--unset", "KEY"]])
def test_invalid_config_does_not_mutate(target, flags):
    result = CliRunner().invoke(manage.config_command, flags)
    assert result.exit_code != 0
    target.update_environment.assert_not_called()


def test_scale_bounds_and_confirmation(target):
    result = CliRunner().invoke(manage.scale_command, ["--min", "2", "--max", "4"], input="n\n")
    assert result.exit_code != 0
    target.update_environment.assert_not_called()
    result = CliRunner().invoke(manage.scale_command, ["--min", "2", "--max", "4", "--yes"])
    assert result.exit_code == 0
    options = target.update_environment.call_args.kwargs["OptionSettings"]
    assert [option["Value"] for option in options] == ["2", "4"]
    assert all(option["Namespace"] == manage.SCALE_NAMESPACE for option in options)


@pytest.mark.parametrize("minimum,maximum", [(0, 2), (4, 2), (1, 101)])
def test_scale_rejects_invalid_bounds(target, minimum, maximum):
    result = CliRunner().invoke(manage.scale_command, ["--min", str(minimum), "--max", str(maximum)])
    assert result.exit_code != 0
    target.update_environment.assert_not_called()


def test_versions_pagination_and_rollback(target):
    target.describe_application_versions.side_effect = [
        {"ApplicationVersions": [{"VersionLabel": "old"}], "NextToken": "next"},
        {"ApplicationVersions": [{"VersionLabel": "new"}]},
    ]
    result = CliRunner().invoke(manage.versions_command)
    assert result.exit_code == 0
    assert "old" in result.output and "new" in result.output
    target.describe_application_versions.side_effect = None
    target.describe_application_versions.return_value = {"ApplicationVersions": [{"VersionLabel": "old"}]}
    result = CliRunner().invoke(manage.rollback_command, ["--version", "old", "--yes"])
    assert result.exit_code == 0
    target.update_environment.assert_called_once_with(EnvironmentName="demo-cluster", VersionLabel="old")


def test_rollback_missing_version_does_not_mutate(target):
    target.describe_application_versions.return_value = {"ApplicationVersions": []}
    result = CliRunner().invoke(manage.rollback_command, ["--version", "missing", "--yes"])
    assert result.exit_code != 0
    target.update_environment.assert_not_called()


def test_aws_error_cannot_print_secret_config(target):
    target.update_environment.side_effect = ClientError({"Error": {"Code": "Failure", "Message": "leaked-secret"}}, "UpdateEnvironment")
    result = CliRunner().invoke(manage.config_command, ["--env", "KEY=leaked-secret", "--yes"])
    assert result.exit_code != 0
    assert "leaked-secret" not in result.output


@pytest.mark.parametrize("deleted", [False, True])
def test_cleanup_status_checks_recorded_stack(monkeypatch, deleted):
    session = MagicMock()
    cf = session.client.return_value
    cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]}
    if deleted:
        cf.describe_stacks.side_effect = ClientError({"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}, "DescribeStacks")
    monkeypatch.setattr(manage.ops, "_aws_session", lambda *args: (session, "account"))
    monkeypatch.setattr(manage.ops, "_matching_deployments", lambda *args: [{"environment_name": "demo", "cluster_arn": "arn/beanstalk-cluster-test"}])
    result = CliRunner().invoke(manage.cleanup_status_command)
    assert result.exit_code == 0
    assert ("stack deleted" if deleted else "Charges may continue") in result.output
    cf.delete_stack.assert_not_called()


def test_resources_json_and_cli_registration(target):
    target.describe_environment_resources.return_value = {"EnvironmentResources": {"Cluster": {"Name": "cluster-arn"}}}
    result = CliRunner().invoke(manage.resources_command, ["--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["environment_resources"]["Cluster"]["Name"] == "cluster-arn"
    help_result = CliRunner().invoke(cli, ["--help"])
    for name in ("scale", "config", "versions", "rollback", "cleanup-status", "resources"):
        assert name in help_result.output


def test_redeploy_preserves_settings_and_scaling(target):
    from ebkit.commands.deploy import _existing_environment_update_options
    options = _existing_environment_update_options(target, "demo", "demo-cluster", {"NEW": "new"}, 8080)
    assert len(options) == 2
    assert all(option["Namespace"] == manage.ENV_NAMESPACE for option in options)
    updated = json.loads(options[1]["Value"])
    assert updated["DATABASE_URL"] == "hidden-existing-secret"
    assert updated["KEEP"] == "yes"
    assert updated["NEW"] == "new"
    assert updated["PORT"] == "8080"
