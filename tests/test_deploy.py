from botocore.exceptions import ParamValidationError
from unittest.mock import MagicMock, patch

from ebkit.commands.deploy import (
    _ensure_cluster_environment,
    _make_application_version_label,
    _register_cluster_version,
)


def test_register_cluster_version_uses_prebuilt_image_configuration():
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
        Description=f"EBReady Cluster deployment: {image_uri}",
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
    assert command[command.index("--image-configuration") + 1] == (
        '{"Source": {"Uri": '
        '"717056864326.dkr.ecr.us-east-2.amazonaws.com/notes-app:v1"}}'
    )
    assert command[command.index("--region") + 1] == "us-east-2"
    assert command[-1] == "--no-cli-pager"


def test_new_cluster_environment_starts_with_requested_version():
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
        port=8000,
    )

    assert created is True
    create_args = eb_client.create_environment.call_args.kwargs
    assert create_args["VersionLabel"] == "notes-app-v1"
    assert create_args["Tier"] == {"Name": "Cluster", "Type": "EKS"}


def test_application_version_labels_are_unique_and_within_aws_limit(monkeypatch):
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
