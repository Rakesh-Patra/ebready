from __future__ import annotations

import json
from unittest.mock import MagicMock

from click.testing import CliRunner

from ebkit.commands.operations import (
    destroy_command,
    diagnose_command,
    envlist_command,
    logs_command,
    _redact_diagnostic_text,
    status_command,
)
from ebkit.deployment_state import load_deployments, remove_deployment, save_deployment
def _environment(**overrides):
    return {
        "ApplicationName": "demo",
        "EnvironmentName": "demo-cluster",
        "Status": "Ready",
        "Health": "Green",
        "HealthStatus": "Ok",
        "CNAME": "demo.example.com",
        "VersionLabel": "demo-v3",
        **overrides,
    }


def test_deployment_state_saves_only_supplied_non_secret_metadata(tmp_path):
    path = tmp_path / "deployments.json"
    record = {
        "account_id": "123456789012",
        "region": "us-east-2",
        "application_name": "demo",
        "environment_name": "demo-cluster",
        "source_url": "https://github.com/example/demo",
        "status": "Ready",
    }

    save_deployment(record, path)
    save_deployment({**record, "status": "Deploying"}, path)

    deployments = load_deployments(path)
    assert len(deployments) == 1
    assert deployments[0]["status"] == "Deploying"
    assert "secret" not in path.read_text(encoding="utf-8").lower()
    remove_deployment("123456789012", "us-east-2", "demo-cluster", path)
    assert load_deployments(path) == []


def test_diagnostic_context_redacts_common_credentials():
    redacted = _redact_diagnostic_text(
        "AWS key AKIA1234567890ABCDEF and password=hunter2"
    )

    assert "AKIA1234567890ABCDEF" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED_AWS_KEY]" in redacted
    assert "password=[REDACTED]" in redacted


def test_status_shows_health_url_and_version(monkeypatch):
    eb = MagicMock()
    eb.describe_environments.return_value = {"Environments": [_environment()]}
    session = MagicMock()
    session.client.return_value = eb
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._resolve_deployment",
        lambda *_args: ("demo", "demo-cluster", {}),
    )

    result = CliRunner().invoke(status_command, [])

    assert result.exit_code == 0, result.output
    assert "Status: Ready" in result.output
    assert "Health: Green (Ok)" in result.output
    assert "https://demo.example.com" in result.output
    assert "Deployed version: demo-v3" in result.output


def test_envlist_paginates_environment_results(monkeypatch):
    eb = MagicMock()
    eb.describe_environments.side_effect = [
        {
            "Environments": [_environment()],
            "NextToken": "next",
        },
        {"Environments": [_environment(EnvironmentName="demo-preview")]},
    ]
    session = MagicMock()
    session.client.return_value = eb
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )

    result = CliRunner().invoke(envlist_command, [])

    assert result.exit_code == 0, result.output
    assert "demo-cluster" in result.output
    assert "demo-preview" in result.output
    assert eb.describe_environments.call_count == 2


def test_logs_show_build_and_deployment_events(monkeypatch):
    eb = MagicMock()
    eb.describe_environments.return_value = {"Environments": [_environment()]}
    eb.describe_events.return_value = {
        "Events": [{"Severity": "INFO", "Message": "Deployment completed"}]
    }
    logs_client = MagicMock()
    logs_client.get_log_events.return_value = {
        "events": [{"message": "Docker image pushed"}]
    }
    session = MagicMock()
    session.client.side_effect = lambda name: {
        "elasticbeanstalk": eb,
        "codebuild": MagicMock(),
        "logs": logs_client,
    }[name]
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._resolve_deployment",
        lambda *_args: (
            "demo",
            "demo-cluster",
            {
                "codebuild_project": "ebkit-demo-image-build",
                "build_id": "project:build-id",
            },
        ),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._get_build",
        lambda *_args: {
            "id": "project:build-id",
            "buildStatus": "SUCCEEDED",
            "projectName": "ebkit-demo-image-build",
            "logs": {"groupName": "/aws/codebuild/demo", "streamName": "stream"},
        },
    )

    result = CliRunner().invoke(logs_command, [])

    assert result.exit_code == 0, result.output
    assert "Docker image pushed" in result.output
    assert "Deployment completed" in result.output


def test_diagnose_uses_gemini_with_bounded_retries(monkeypatch):
    eb = MagicMock()
    eb.describe_environments.return_value = {"Environments": [_environment()]}
    eb.describe_events.return_value = {
        "Events": [{"Severity": "ERROR", "Message": "Build failed"}]
    }
    session = MagicMock()
    session.client.return_value = eb
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._resolve_deployment",
        lambda *_args: ("demo", "demo-cluster", {}),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._get_build",
        lambda *_args: None,
    )
    gemini = MagicMock(return_value="Root cause: invalid Docker build.")
    monkeypatch.setattr("ebkit.commands.operations._gemini_diagnose", gemini)

    result = CliRunner().invoke(
        diagnose_command,
        ["--max-retries", "1", "--gemini"],
    )

    assert result.exit_code == 0, result.output
    assert "Gemini diagnosis" in result.output
    assert "does not automatically modify" in result.output.lower()
    gemini.assert_called_once()
    assert gemini.call_args.args[1] == 1


def test_destroy_requires_exact_environment_confirmation(monkeypatch):
    eb = MagicMock()
    eb.describe_environments.return_value = {"Environments": [_environment()]}
    session = MagicMock()
    session.client.return_value = eb
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._resolve_deployment",
        lambda *_args: ("demo", "demo-cluster", {}),
    )

    result = CliRunner().invoke(destroy_command, [], input="demo\n")

    assert result.exit_code != 0
    assert "Aborted" in result.output
    eb.terminate_environment.assert_not_called()


def test_destroy_terminates_only_selected_environment_and_keeps_shared_resources(
    monkeypatch, tmp_path
):
    eb = MagicMock()
    eb.describe_environments.return_value = {"Environments": [_environment()]}
    session = MagicMock()
    session.client.return_value = eb
    monkeypatch.setenv("EBKIT_STATE_FILE", str(tmp_path / "deployments.json"))
    monkeypatch.setattr(
        "ebkit.commands.operations._aws_session",
        lambda _region: (session, "123456789012"),
    )
    monkeypatch.setattr(
        "ebkit.commands.operations._resolve_deployment",
        lambda *_args: (
            "demo",
            "demo-cluster",
            {
                "account_id": "123456789012",
                "region": "us-east-2",
                "application_name": "demo",
                "environment_name": "demo-cluster",
                "codebuild_project": "shared-build",
            },
        ),
    )

    result = CliRunner().invoke(destroy_command, [], input="demo-cluster\n")

    assert result.exit_code == 0, result.output
    eb.terminate_environment.assert_called_once_with(EnvironmentName="demo-cluster")
    assert "databases untouched" in result.output
    state = json.loads((tmp_path / "deployments.json").read_text(encoding="utf-8"))
    assert state["deployments"][0]["status"] == "Terminating"
    assert state["deployments"][0]["codebuild_project"] == "shared-build"
