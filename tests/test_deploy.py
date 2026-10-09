import json
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner
from botocore.exceptions import ParamValidationError

from ebkit.commands.deploy import (
    _build_cluster_option_settings,
    _build_image_in_aws,
    _codebuild_names,
    _collect_env_vars,
    _ensure_cluster_environment,
    _execute_deploy,
    _make_application_version_label,
    _register_cluster_version,
    _wait_for_codebuild,
    deploy,
)


def test_codebuild_names_are_bounded_and_repository_specific():
    project, role = _codebuild_names(
        "a-very-long-application-name-" * 3,
        "https://github.com/ExampleOwner/ExampleRepo.git",
    )
    other_project, _ = _codebuild_names(
        "a-very-long-application-name-" * 3,
        "https://github.com/ExampleOwner/AnotherRepo.git",
    )

    assert project.startswith("ebkit-a-very-long-")
    assert project.endswith("-image-build")
    assert project != other_project
    assert len(project) <= 64
    assert len(role) <= 64


def test_github_deployment_builds_and_pushes_image_in_codebuild():
    iam_client = MagicMock()
    codebuild_client = MagicMock()
    codebuild_client.start_build.return_value = {"build": {"id": "project:build-id"}}
    codebuild_client.batch_get_builds.return_value = {
        "builds": [{"buildStatus": "SUCCEEDED"}]
    }

    with patch(
        "ebkit.commands.deploy._ensure_iam_role",
        return_value="arn:aws:iam::123456789012:role/build-role",
    ):
        _build_image_in_aws(
            iam_client=iam_client,
            codebuild_client=codebuild_client,
            account_id="123456789012",
            region="us-east-2",
            app_name="notes-app",
            repo_name="notes-app",
            repo_url="https://github.com/ExampleOwner/ExampleRepo",
            image_uri=(
                "123456789012.dkr.ecr.us-east-2.amazonaws.com/notes-app:v1"
            ),
        )

    project = codebuild_client.create_project.call_args.kwargs
    assert project["source"]["type"] == "GITHUB"
    assert project["source"]["location"] == (
        "https://github.com/ExampleOwner/ExampleRepo.git"
    )
    assert project["environment"]["privilegedMode"] is True
    assert "docker build --platform linux/amd64" in project["source"]["buildspec"]
    assert codebuild_client.batch_get_builds.call_count == 1


def test_deploy_rejects_local_paths_and_removed_deployment_options():
    runner = CliRunner()

    local_path = runner.invoke(deploy, ["."])

    assert local_path.exit_code != 0
    assert "public GitHub repository URL" in local_path.output
    for option in ("--compose", "--no-build", "--no-push", "--tag"):
        removed_option = runner.invoke(
            deploy, ["https://github.com/example/app", option]
        )
        assert removed_option.exit_code != 0
        assert f"No such option: {option}" in removed_option.output


def test_deploy_requires_source_url():
    result = CliRunner().invoke(deploy, [])

    assert result.exit_code != 0
    assert "Missing argument 'SOURCE'" in result.output


def test_execute_deploy_uses_codebuild_and_never_runs_local_docker(monkeypatch):
    monkeypatch.setattr("ebkit.commands.deploy.load_config", lambda: None)
    monkeypatch.setattr(
        "ebkit.commands.deploy._check_aws_credentials",
        lambda: (True, "123456789012", None),
    )
    monkeypatch.setattr(
        "ebkit.commands.deploy._collect_env_vars",
        lambda *_args: {},
    )
    monkeypatch.setattr("ebkit.commands.deploy._ensure_ecr_repo", lambda *_args: "ecr-uri")
    monkeypatch.setattr(
        "ebkit.commands.deploy._provision_cluster_infrastructure",
        lambda *_args: {
            "cluster_role": "cluster",
            "node_role": "node",
            "operation_role": "operation",
            "observability_role": "observability",
            "subnets": [],
        },
    )
    monkeypatch.setattr("ebkit.commands.deploy._ensure_eb_application", lambda *_args: None)
    monkeypatch.setattr("ebkit.commands.deploy._register_cluster_version", lambda *_args: None)
    monkeypatch.setattr("ebkit.commands.deploy._ensure_cluster_environment", lambda *_args: True)
    session = MagicMock()
    monkeypatch.setattr("ebkit.commands.deploy.boto3.Session", lambda **_kwargs: session)

    with (
        patch("ebkit.commands.deploy._build_image_in_aws") as build_in_aws,
        patch("ebkit.commands.deploy.subprocess.run") as local_command,
    ):
        _execute_deploy(
            source_url="https://github.com/example/notes-app",
            app="notes-app",
            env="notes-app-cluster",
            region="us-east-2",
            port=5000,
            wait=False,
            env_file=None,
            extra_env=(),
            is_interactive=False,
        )

    assert build_in_aws.call_args.kwargs["repo_url"] == (
        "https://github.com/example/notes-app"
    )
    assert build_in_aws.call_args.kwargs["image_uri"].startswith(
        "123456789012.dkr.ecr.us-east-2.amazonaws.com/notes-app:v"
    )
    assert local_command.call_count == 0


def test_codebuild_failure_reports_status_and_logs_url():
    codebuild_client = MagicMock()
    codebuild_client.batch_get_builds.return_value = {
        "builds": [
            {
                "buildStatus": "FAILED",
                "logs": {"deepLink": "https://console.aws.amazon.com/codebuild/log"},
            }
        ]
    }

    try:
        _wait_for_codebuild(codebuild_client, "project:failed-build")
    except RuntimeError as exc:
        assert "FAILED" in str(exc)
        assert "https://console.aws.amazon.com/codebuild/log" in str(exc)
    else:
        raise AssertionError("Expected a failed CodeBuild build to raise.")


def test_register_cluster_version_uses_image_configuration():
    eb_client = MagicMock()
    image_uri = "717056864326.dkr.ecr.us-east-2.amazonaws.com/notes-app:v1"

    _register_cluster_version(
        eb_client,
        app_name="notes-app",
        version_label="notes-app-v1",
        image_uri=image_uri,
        region="us-east-2",
    )

    eb_client.create_application_version.assert_called_once_with(
        ApplicationName="notes-app",
        VersionLabel="notes-app-v1",
        Description=f"EBKit Cluster deployment: {image_uri}",
        ImageConfiguration={"Source": {"Uri": image_uri}},
        AutoCreateApplication=True,
    )


def test_register_cluster_version_falls_back_to_aws_cli_for_old_sdk():
    eb_client = MagicMock()
    eb_client.create_application_version.side_effect = ParamValidationError(
        report="Unknown parameter: ImageConfiguration"
    )
    cli_result = MagicMock(returncode=0, stderr="", stdout="{}")

    with patch("ebkit.commands.deploy.subprocess.run", return_value=cli_result) as run:
        _register_cluster_version(
            eb_client,
            app_name="notes-app",
            version_label="notes-app-v1",
            image_uri="717056864326.dkr.ecr.us-east-2.amazonaws.com/notes-app:v1",
            region="us-east-2",
        )

    command = run.call_args.args[0]
    assert command[0:3] == ["aws", "elasticbeanstalk", "create-application-version"]
    assert command[command.index("--region") + 1] == "us-east-2"
    assert command[-1] == "--no-cli-pager"


def test_new_cluster_environment_uses_cluster_tier_and_service_port():
    eb_client = MagicMock()
    eb_client.describe_environments.return_value = {"Environments": []}
    infra = {
        "cluster_role": "cluster-role",
        "node_role": "node-role",
        "operation_role": "operation-role",
        "observability_role": "observability-role",
        "subnets": [],
    }

    created = _ensure_cluster_environment(
        eb_client,
        app_name="notes-app",
        env_name="notes-app-cluster",
        version_label="notes-app-v1",
        infra=infra,
        env_vars={},
        port=5000,
    )

    assert created is True
    create_args = eb_client.create_environment.call_args.kwargs
    assert create_args["VersionLabel"] == "notes-app-v1"
    assert create_args["Tier"] == {"Name": "Cluster", "Type": "EKS"}
    env_setting = next(
        option for option in create_args["OptionSettings"]
        if option["OptionName"] == "service-port"
    )
    assert env_setting["Value"] == "5000"


def test_cluster_environment_variables_include_service_port():
    settings = _build_cluster_option_settings(
        {
            "cluster_role": "cluster",
            "node_role": "node",
            "operation_role": "operation",
            "observability_role": "observability",
            "subnets": [],
        },
        {"DATABASE_URL": "postgresql://db.example.com/app"},
        port=5000,
    )
    env_options = [option for option in settings if option["OptionName"] == "env-variables"]

    assert len(env_options) == 1
    assert json.loads(env_options[0]["Value"]) == {
        "DATABASE_URL": "postgresql://db.example.com/app",
        "PORT": "5000",
    }


def test_application_version_label_is_unique_and_within_aws_limit(monkeypatch):
    timestamps = iter((1791464602000000000, 1791464602000000001))
    monkeypatch.setattr("ebkit.commands.deploy.time.time_ns", lambda: next(timestamps))
    image_uri = (
        "717056864326.dkr.ecr.us-east-2.amazonaws.com/notes-app:"
        + "x" * 128
    )

    first = _make_application_version_label(image_uri)
    second = _make_application_version_label(image_uri)

    assert len(first) <= 100
    assert first != second


def test_env_file_and_explicit_environment_are_combined(tmp_path):
    env_file = tmp_path / ".env.production"
    env_file.write_text("DATABASE_URL=postgresql://db.example.com/app\n", encoding="utf-8")

    values = _collect_env_vars(
        str(env_file),
        ("PORT=5000",),
        is_interactive=False,
    )

    assert values == {
        "DATABASE_URL": "postgresql://db.example.com/app",
        "PORT": "5000",
    }


def test_missing_env_file_fails_explicitly(tmp_path):
    try:
        _collect_env_vars(str(tmp_path / "missing.env"), (), is_interactive=False)
    except click.ClickException as exc:
        assert "Environment file not found" in str(exc)
    else:
        raise AssertionError("Expected a missing env file to fail.")
