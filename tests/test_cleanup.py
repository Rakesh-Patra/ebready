from unittest.mock import MagicMock

from ebkit.cleanup import cleanup_environment_logs, cleanup_application_artifacts


def test_shared_cloudwatch_groups_only_delete_exact_environment_streams():
    logs = MagicMock()
    logs.describe_log_streams.side_effect = [
        {"logStreams": [{"logStreamName": "eb-demo.pod"}], "nextToken": "next"},
        {"logStreams": [{"logStreamName": "eb-demo-other.pod"}, {"logStreamName": "eb-demo"}]},
        {}, {}, {},
    ]
    deleted = cleanup_environment_logs(logs, "demo")
    assert len(deleted) == 2
    assert {call.kwargs["logStreamName"] for call in logs.delete_log_stream.call_args_list} == {"eb-demo", "eb-demo.pod"}
    logs.delete_log_group.assert_not_called()


def test_active_environment_prevents_application_artifact_deletion():
    session = MagicMock()
    session.client.return_value.describe_environments.return_value = {
        "Environments": [{"Status": "Ready"}]
    }
    messages = cleanup_application_artifacts(session, "demo", {})
    assert "another environment" in messages[0]
    assert session.client.call_count == 1


def test_owned_artifacts_deleted_and_running_build_stopped():
    clients = {name: MagicMock() for name in ("elasticbeanstalk", "codebuild", "ecr", "logs")}
    session = MagicMock()
    session.client.side_effect = clients.__getitem__
    clients["elasticbeanstalk"].describe_environments.return_value = {"Environments": []}
    clients["codebuild"].batch_get_projects.return_value = {"projects": [{"tags": [
        {"key": "ManagedBy", "value": "EBKit"}, {"key": "EBKitApplication", "value": "demo"}
    ]}]}
    clients["codebuild"].list_builds_for_project.return_value = {"ids": ["build"]}
    clients["codebuild"].batch_get_builds.return_value = {"builds": [{"id": "build", "buildStatus": "IN_PROGRESS"}]}
    clients["elasticbeanstalk"].describe_applications.return_value = {"Applications": []}
    clients["ecr"].describe_repositories.return_value = {"repositories": [{"repositoryArn": "repo-arn"}]}
    clients["ecr"].list_tags_for_resource.return_value = {"tags": [
        {"Key": "ManagedBy", "Value": "EBKit"}, {"Key": "EBKitApplication", "Value": "demo"}
    ]}
    cleanup_application_artifacts(session, "demo", {"codebuild_project": "project", "build_id": "build"})
    clients["codebuild"].stop_build.assert_called_once_with(id="build")
    clients["codebuild"].delete_project.assert_called_once_with(name="project")
    clients["ecr"].delete_repository.assert_called_once_with(repositoryName="demo", force=True)
    clients["logs"].delete_log_group.assert_called_once_with(logGroupName="/aws/codebuild/project")


def test_unowned_resources_are_retained():
    clients = {name: MagicMock() for name in ("elasticbeanstalk", "codebuild", "ecr")}
    session = MagicMock()
    session.client.side_effect = clients.__getitem__
    clients["elasticbeanstalk"].describe_environments.return_value = {"Environments": []}
    clients["codebuild"].batch_get_projects.return_value = {"projects": [{"tags": []}]}
    clients["ecr"].describe_repositories.return_value = {"repositories": [{"repositoryArn": "repo-arn"}]}
    clients["ecr"].list_tags_for_resource.return_value = {"tags": []}
    clients["elasticbeanstalk"].describe_applications.return_value = {"Applications": []}
    messages = cleanup_application_artifacts(session, "demo", {"codebuild_project": "shared"})
    assert len(messages) == 2
    clients["codebuild"].delete_project.assert_not_called()
    clients["ecr"].delete_repository.assert_not_called()
