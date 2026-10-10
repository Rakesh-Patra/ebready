"""Clean up confirmed deployment artifacts without changing managed EKS infrastructure."""
from botocore.exceptions import ClientError


CLUSTER_LOG_GROUPS = [
    f"/aws/elasticbeanstalk/{category}/{kind}"
    for category in ("application", "infrastructure")
    for kind in ("logs", "metrics")
]


def cleanup_environment_logs(logs, environment):
    """Delete only this namespace's streams in shared Cluster log groups."""
    deleted = []
    namespace = f"eb-{environment}"
    for group in CLUSTER_LOG_GROUPS:
        token = None
        streams = []
        try:
            while True:
                args = {"logGroupName": group, "logStreamNamePrefix": namespace}
                if token:
                    args["nextToken"] = token
                page = logs.describe_log_streams(**args)
                streams.extend(item["logStreamName"] for item in page.get("logStreams", [])
                               if item["logStreamName"] == namespace
                               or item["logStreamName"].startswith(namespace + "."))
                next_token = page.get("nextToken")
                if not next_token or next_token == token:
                    break
                token = next_token
            for stream in streams:
                logs.delete_log_stream(logGroupName=group, logStreamName=stream)
                deleted.append(f"{group}:{stream}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
    return deleted


def cleanup_application_artifacts(session, app, record):
    """Delete artifacts only when the app has no active environments and ownership matches."""
    eb = session.client("elasticbeanstalk")
    active = eb.describe_environments(ApplicationName=app, IncludeDeleted=False).get("Environments", [])
    if any(item.get("Status") != "Terminated" for item in active):
        return ["Retained application artifacts: another environment still uses this application."]
    messages = []
    project = record.get("codebuild_project")
    if project:
        codebuild = session.client("codebuild")
        projects = codebuild.batch_get_projects(names=[project]).get("projects", [])
        if projects:
            tags = {tag["key"]: tag["value"] for tag in projects[0].get("tags", [])}
            if tags.get("ManagedBy") == "EBKit" and tags.get("EBKitApplication") == app:
                # A build may continue charging after its environment is terminated.
                build_ids = set()
                if record.get("build_id"):
                    build_ids.add(record["build_id"])
                token = None
                while True:
                    args = {"projectName": project}
                    if token:
                        args["nextToken"] = token
                    page = codebuild.list_builds_for_project(**args)
                    build_ids.update(page.get("ids", []))
                    next_token = page.get("nextToken")
                    if not next_token or next_token == token:
                        break
                    token = next_token
                ordered_ids = sorted(build_ids)
                for offset in range(0, len(ordered_ids), 100):
                    builds = codebuild.batch_get_builds(ids=ordered_ids[offset:offset + 100]).get("builds", [])
                    for build in builds:
                        if build.get("buildStatus") == "IN_PROGRESS":
                            codebuild.stop_build(id=build["id"])
                codebuild.delete_project(name=project)
                try:
                    session.client("logs").delete_log_group(logGroupName=f"/aws/codebuild/{project}")
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
                messages.append(f"Deleted CodeBuild project and its log group: {project}")
            else:
                messages.append(f"Retained unverified/shared CodeBuild project: {project}")
    ecr = session.client("ecr")
    try:
        repos = ecr.describe_repositories(repositoryNames=[app]).get("repositories", [])
        if repos:
            tags = {tag["Key"]: tag["Value"] for tag in ecr.list_tags_for_resource(
                resourceArn=repos[0]["repositoryArn"]).get("tags", [])}
            if tags.get("ManagedBy") == "EBKit" and tags.get("EBKitApplication") == app:
                ecr.delete_repository(repositoryName=app, force=True)
                messages.append(f"Deleted ECR repository and all images: {app}")
            else:
                messages.append(f"Retained unverified/shared ECR repository: {app}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
    apps = eb.describe_applications(ApplicationNames=[app]).get("Applications", [])
    if apps and apps[0].get("Description") == "Managed by EBKit":
        eb.delete_application(ApplicationName=app, TerminateEnvByForce=False)
        messages.append(f"Deleted empty EBKit application and its versions: {app}")
    return messages
