"""Read-only deployment inspection, diagnostics, and guarded teardown commands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError

from ebkit.analyzer.diagnosis import get_diagnosis_engine
from ebkit.commands.deploy import _codebuild_names
from ebkit.config import load_config
from ebkit.deployment_state import load_deployments, save_deployment
from ebkit.cleanup import cleanup_environment_logs, cleanup_application_artifacts
from ebkit.aws_resources import environment_resources

_MAX_LOG_LINES = 500
_MAX_DIAGNOSIS_RETRIES = 3


def _region_option(region: str | None) -> str:
    config = load_config()
    return (
        region
        or (getattr(config, "aws_region", None) if config else None)
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-2"
    )


def _aws_session(region: str):
    session = boto3.Session(region_name=region)
    try:
        account_id = session.client("sts").get_caller_identity()["Account"]
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not authenticate to AWS: {exc}") from exc
    return session, account_id


def _matching_deployments(
    account_id: str,
    region: str,
    app: str | None,
    environment: str | None,
) -> list[dict[str, Any]]:
    try:
        entries = load_deployments()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    matches = [
        entry
        for entry in entries
        if entry.get("account_id") == account_id
        and entry.get("region") == region
        and (not app or entry.get("application_name") == app)
        and (not environment or entry.get("environment_name") == environment)
    ]
    return matches


def _configured_names() -> tuple[str | None, str | None]:
    config = load_config()
    return (
        getattr(config, "aws_application", None) if config else None,
        getattr(config, "aws_environment", None) if config else None,
    )


def _resolve_deployment(
    session,
    account_id: str,
    region: str,
    app: str | None,
    environment: str | None,
    include_deleted: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    entries = _matching_deployments(account_id, region, app, environment)
    record: dict[str, Any] = {}
    if entries:
        record = entries[-1]
        app = app or record.get("application_name")
        environment = environment or record.get("environment_name")

    configured_app, configured_environment = _configured_names()
    app = app or configured_app
    environment = environment or configured_environment
    if not app or not environment:
        raise click.ClickException(
            "Specify --app and --environment, or deploy with EBKit first so it can "
            "resolve the target environment."
        )

    eb = session.client("elasticbeanstalk")
    try:
        response = eb.describe_environments(
            ApplicationName=app,
            EnvironmentNames=[environment],
            IncludeDeleted=include_deleted,
        )
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not inspect Elastic Beanstalk environment: {exc}") from exc
    environments = response.get("Environments", [])
    if not environments:
        raise click.ClickException(
            f"Elastic Beanstalk environment '{environment}' was not found in {region}."
        )
    return app, environment, record


def _get_build(session, record: dict[str, Any]) -> dict[str, Any] | None:
    project_name = record.get("codebuild_project")
    if not project_name:
        source_url = record.get("source_url")
        app_name = record.get("application_name")
        if source_url and app_name:
            project_name = _codebuild_names(app_name, source_url)[0]
    if not project_name:
        return None

    codebuild = session.client("codebuild")
    build_id = record.get("build_id")
    try:
        if not build_id:
            builds = codebuild.list_builds_for_project(
                projectName=project_name,
                sortOrder="DESCENDING",
                maxResults=1,
            ).get("ids", [])
            if not builds:
                return None
            build_id = builds[0]
        builds = codebuild.batch_get_builds(ids=[build_id]).get("builds", [])
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not retrieve CodeBuild details: {exc}") from exc
    if not builds:
        return None
    build = dict(builds[0])
    build.setdefault("projectName", project_name)
    return build


def _get_build_log_lines(session, build: dict[str, Any], limit: int) -> list[str]:
    logs = build.get("logs", {})
    group = logs.get("groupName") or f"/aws/codebuild/{build.get('projectName', '')}"
    stream = logs.get("streamName")
    if not stream:
        return []
    try:
        response = session.client("logs").get_log_events(
            logGroupName=group,
            logStreamName=stream,
            limit=limit,
            startFromHead=False,
        )
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not retrieve CodeBuild logs: {exc}") from exc
    return [event.get("message", "") for event in response.get("events", [])]


def _deployment_events(eb, environment: str, limit: int) -> list[dict[str, Any]]:
    try:
        response = eb.describe_events(EnvironmentName=environment, MaxRecords=limit)
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not retrieve deployment events: {exc}") from exc
    return response.get("Events", [])


@click.command("status")
@click.option("--app", help="Elastic Beanstalk application name.")
@click.option("--environment", "--env-name", "environment_name", help="Environment name.")
@click.option("--region", help="AWS region; defaults to saved AWS configuration.")
def status_command(app: str | None, environment_name: str | None, region: str | None) -> None:
    """Show environment health, deployment status, URL, and deployed version."""
    selected_region = _region_option(region)
    session, account_id = _aws_session(selected_region)
    app_name, env_name, _ = _resolve_deployment(
        session, account_id, selected_region, app, environment_name
    )
    eb = session.client("elasticbeanstalk")
    try:
        environments = eb.describe_environments(
            ApplicationName=app_name,
            EnvironmentNames=[env_name],
            IncludeDeleted=False,
        ).get("Environments", [])
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not retrieve environment status: {exc}") from exc
    if not environments:
        raise click.ClickException(f"Elastic Beanstalk environment '{env_name}' was not found.")

    environment = environments[0]
    click.echo(f"Application: {app_name}")
    click.echo(f"Environment: {environment.get('EnvironmentName', env_name)}")
    click.echo(f"Status: {environment.get('Status', 'Unknown')}")
    click.echo(f"Health: {environment.get('Health', 'Unknown')} ({environment.get('HealthStatus', 'Unknown')})")
    cname = environment.get("CNAME")
    click.echo(f"Application URL: {f'https://{cname}' if cname else 'Not available'}")
    click.echo(f"Deployed version: {environment.get('VersionLabel', 'Unknown')}")


@click.command("envlist")
@click.option("--region", help="AWS region; defaults to saved AWS configuration.")
def envlist_command(region: str | None) -> None:
    """List Elastic Beanstalk environments in the configured AWS account and region."""
    selected_region = _region_option(region)
    session, _ = _aws_session(selected_region)
    eb = session.client("elasticbeanstalk")
    rows: list[dict[str, str]] = []
    next_token: str | None = None
    try:
        while True:
            args: dict[str, Any] = {"IncludeDeleted": False, "MaxRecords": 1000}
            if next_token:
                args["NextToken"] = next_token
            response = eb.describe_environments(**args)
            for environment in response.get("Environments", []):
                cname = environment.get("CNAME")
                rows.append(
                    {
                        "Application": environment.get("ApplicationName", ""),
                        "Environment": environment.get("EnvironmentName", ""),
                        "Status": environment.get("Status", "Unknown"),
                        "Health": environment.get("Health", "Unknown"),
                        "URL": f"https://{cname}" if cname else "",
                    }
                )
            next_token = response.get("NextToken")
            if not next_token:
                break
    except (BotoCoreError, ClientError) as exc:
        raise click.ClickException(f"Could not list Elastic Beanstalk environments: {exc}") from exc
    if not rows:
        click.echo(f"No Elastic Beanstalk environments found in {selected_region}.")
        return
    widths = {
        column: max(len(column), *(len(row[column]) for row in rows))
        for column in ("Application", "Environment", "Status", "Health", "URL")
    }
    click.echo("  ".join(column.ljust(widths[column]) for column in widths))
    click.echo("  ".join("-" * widths[column] for column in widths))
    for row in rows:
        click.echo("  ".join(row[column].ljust(widths[column]) for column in widths))


@click.command("logs")
@click.option("--app", help="Elastic Beanstalk application name.")
@click.option("--environment", "--env-name", "environment_name", help="Environment name.")
@click.option("--region", help="AWS region; defaults to saved AWS configuration.")
@click.option(
    "--source",
    "log_source",
    type=click.Choice(["all", "build", "deployment", "application"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Show build logs, deployment events, application container logs, or all three.",
)
@click.option(
    "--lines",
    default=100,
    show_default=True,
    type=click.IntRange(1, _MAX_LOG_LINES),
    help=f"Maximum number of build log lines (1-{_MAX_LOG_LINES}).",
)
def logs_command(
    app: str | None,
    environment_name: str | None,
    region: str | None,
    log_source: str,
    lines: int,
) -> None:
    """Retrieve CodeBuild and Elastic Beanstalk deployment logs."""
    selected_region = _region_option(region)
    session, account_id = _aws_session(selected_region)
    app_name, env_name, record = _resolve_deployment(
        session, account_id, selected_region, app, environment_name
    )
    source = log_source.lower()
    unavailable_sources = []
    if source in ("all", "build"):
        build = _get_build(session, record)
        if not build:
            click.echo("No CodeBuild build is recorded for this deployment.")
        else:
            click.echo(
                f"CodeBuild {build.get('id', '')} [{build.get('buildStatus', 'Unknown')}]:"
            )
            try:
                build_lines = _get_build_log_lines(session, build, lines)
            except click.ClickException:
                click.echo("Build logs unavailable. Check logs:GetLogEvents permission for the CodeBuild log group.")
                unavailable_sources.append("build logs")
                build_lines = []
            if build_lines:
                click.echo("\n".join(build_lines))
            else:
                click.echo("No CodeBuild log stream is available yet.")
    if source in ("all", "deployment"):
        eb = session.client("elasticbeanstalk")
        events = _deployment_events(eb, env_name, lines)
        click.echo(f"\nElastic Beanstalk events for {app_name}/{env_name}:")
        if not events:
            click.echo("No deployment events were returned.")
        for event in events:
            timestamp = event.get("EventDate", "")
            severity = event.get("Severity", "")
            message = event.get("Message", "")
            click.echo(f"{timestamp} [{severity}] {message}")
    if source in ("all", "application"):
        click.echo(f"\nApplication container logs for {env_name} (last hour):")
        try:
            events = []
            token = None
            while True:
                args = {
                    "logGroupName": "/aws/elasticbeanstalk/application/logs",
                    "logStreamNamePrefix": f"eb-{env_name}.",
                    "startTime": int((time.time() - 3600) * 1000),
                    "limit": lines,
                }
                if token:
                    args["nextToken"] = token
                page = session.client("logs").filter_log_events(**args)
                events.extend(page.get("events", []))
                next_token = page.get("nextToken")
                if not next_token or next_token == token:
                    break
                token = next_token
            for event in sorted(events, key=lambda item: item.get("timestamp", 0))[-lines:]:
                click.echo(event.get("message", ""))
            if not events:
                click.echo("No application logs found. The application may not have emitted logs or uses another logging backend.")
        except (BotoCoreError, ClientError) as exc:
            if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                click.echo("Application CloudWatch log group is not available yet.")
            else:
                click.echo("Application logs unavailable. Check logs:FilterLogEvents permission for the application log group.")
                unavailable_sources.append("application logs")
    if unavailable_sources:
        raise click.ClickException("Some requested sources could not be read: " + ", ".join(unavailable_sources))


def _redact_diagnostic_text(text: str) -> str:
    text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]", text)
    text = re.sub(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)"
        r"(\s*[=:]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text[-12000:]


def _gemini_diagnose(error_context: str, retries: int) -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise click.ClickException(
            "Gemini diagnosis requires GEMINI_API_KEY or GOOGLE_API_KEY."
        )

    prompt = (
        "Diagnose this AWS Elastic Beanstalk Cluster Mode deployment failure. "
        "Return concise sections named Root cause, Evidence, Recommended fix, and "
        "Safe automated action. Never request credentials, expose secrets, or "
        "suggest deleting shared infrastructure or databases. The tool will not "
        "automatically execute infrastructure or source-code changes.\n\n"
        f"Sanitized diagnostic events and build log tail:\n{_redact_diagnostic_text(error_context)}"
    )
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={api_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 503) or attempt >= retries:
                break
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            last_error = exc
            if attempt >= retries:
                break
        if attempt < retries:
            time.sleep(min(2**attempt, 4))
    raise click.ClickException(
        f"Gemini diagnosis failed after {retries + 1} attempt(s): {last_error}"
    )


@click.command("diagnose")
@click.option("--app", help="Elastic Beanstalk application name.")
@click.option("--environment", "--env-name", "environment_name", help="Environment name.")
@click.option("--region", help="AWS region; defaults to saved AWS configuration.")
@click.option(
    "--max-retries",
    default=2,
    show_default=True,
    type=click.IntRange(0, _MAX_DIAGNOSIS_RETRIES),
    help=f"Maximum bounded Gemini retries (0-{_MAX_DIAGNOSIS_RETRIES}).",
)
@click.option(
    "--gemini",
    "use_gemini",
    is_flag=True,
    help="Send sanitized event and build-log context to Gemini for additional recommendations.",
)
def diagnose_command(
    app: str | None,
    environment_name: str | None,
    region: str | None,
    max_retries: int,
    use_gemini: bool,
) -> None:
    """Analyze the latest deployment failures and suggest bounded, safe next steps."""
    selected_region = _region_option(region)
    session, account_id = _aws_session(selected_region)
    app_name, env_name, record = _resolve_deployment(
        session, account_id, selected_region, app, environment_name
    )
    eb = session.client("elasticbeanstalk")
    events = _deployment_events(eb, env_name, 50)
    build = _get_build(session, record)
    try:
        build_lines = _get_build_log_lines(session, build, 200) if build else []
    except click.ClickException:
        click.echo("Build logs unavailable; diagnosis will use deployment events and build status. Check logs:GetLogEvents permission.")
        build_lines = []
    error_parts = [
        f"{event.get('Severity', '')}: {event.get('Message', '')}"
        for event in events
        if str(event.get("Severity", "")).lower() in {"error", "fatal", "severe"}
    ]
    if not error_parts and build and build.get("buildStatus") == "SUCCEEDED":
        click.echo("No deployment error events found; the latest recorded CodeBuild build succeeded.")
        click.echo("Use ebkit status for live health and ebkit logs --source application for runtime issues.")
        return
    if build:
        error_parts.append(f"CodeBuild status: {build.get('buildStatus', 'Unknown')}")
        error_parts.extend(build_lines[-100:])
    if not error_parts:
        error_parts.extend(str(event.get("Message", "")) for event in events[-10:])
    error_context = "\n".join(part for part in error_parts if part).strip()
    if not error_context:
        raise click.ClickException("No deployment errors or recent events were found to diagnose.")

    fallback = get_diagnosis_engine(prefer_gordon=False).diagnose(error_context)
    click.echo(f"Rule-based root cause: {fallback.root_cause}")
    click.echo(f"Recommended check: {fallback.recommended_fix}")
    click.echo(f"Severity: {fallback.severity}")
    if use_gemini:
        try:
            recommendation = _gemini_diagnose(error_context, max_retries)
        except click.ClickException as exc:
            click.echo(f"Gemini diagnosis unavailable: {exc}")
        else:
            click.echo("\nGemini diagnosis:")
            click.echo(recommendation)
    else:
        click.echo(
            "\nGemini was not requested. Use --gemini to send sanitized diagnostic "
            "events and build-log context for additional recommendations."
        )
    click.echo(
        "\nEBKit does not automatically modify application code, IAM, shared infrastructure, "
        "or databases. Review recommendations and deploy an intentional change separately."
    )


@click.command("destroy")
@click.option("--app", help="Elastic Beanstalk application name.")
@click.option("--environment", "--env-name", "environment_name", help="Environment name.")
@click.option("--region", help="AWS region; defaults to saved AWS configuration.")
@click.option("--keep-artifacts", is_flag=True, help="Only request termination; retain images, build projects and logs.")
@click.option("--timeout", default=1200, type=click.IntRange(1), help="Seconds to wait for environment termination before cleanup.")
def destroy_command(
    app: str | None,
    environment_name: str | None,
    region: str | None,
    keep_artifacts: bool,
    timeout: int,
) -> None:
    """Terminate an environment and clean up eligible EBKit artifacts after confirmation."""
    selected_region = _region_option(region)
    session, account_id = _aws_session(selected_region)
    app_name, env_name, deployment = _resolve_deployment(
        session, account_id, selected_region, app, environment_name, True
    )
    click.echo(
        f"This will terminate environment '{env_name}' in application '{app_name}' "
        f"({selected_region})."
    )
    click.echo(
        "EBKit retains shared infrastructure, IAM roles and external databases. "
        "Elastic Beanstalk deletes the managed EKS cluster three hours after its last environment is terminated; "
        "cluster charges continue until deletion completes."
    )
    if not keep_artifacts:
        click.echo("After termination, delete this environment's CloudWatch streams and eligible EBKit-tagged "
                   "ECR images/repository, CodeBuild project and build logs when no application environments remain. "
                   "An empty application created by EBKit and its versions are also removed. "
                   "Stored logs and images will be permanently removed.")
    if click.prompt("Type the exact environment name to confirm", type=str) != env_name:
        raise click.Abort()
    eb = session.client("elasticbeanstalk")
    try:
        current = eb.describe_environments(EnvironmentNames=[env_name], IncludeDeleted=True).get("Environments", [])
        already_terminated = bool(current) and all(item.get("Status") == "Terminated" for item in current)
        cluster_arn = deployment.get("cluster_arn")
        if not already_terminated:
            resources = environment_resources(eb, env_name, selected_region)
            cluster_arn = resources.get("Cluster", {}).get("ClusterArn") or resources.get("Cluster", {}).get("Name")
            eb.terminate_environment(EnvironmentName=env_name)
    except (BotoCoreError, ClientError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(f"Could not terminate environment '{env_name}': {exc}") from exc
    entries = _matching_deployments(account_id, selected_region, app_name, env_name)
    deployment = deployment or (entries[-1] if entries else {
        "account_id": account_id,
        "region": selected_region,
        "application_name": app_name,
        "environment_name": env_name,
    })
    deployment["status"] = "Terminating"
    if isinstance(cluster_arn, str) and cluster_arn:
        deployment["cluster_arn"] = cluster_arn
    try:
        save_deployment(deployment)
    except (OSError, ValueError) as exc:
        raise click.ClickException(
            f"Environment termination started, but local deployment state could not be updated: {exc}"
        ) from exc
    click.echo(f"Termination requested for '{env_name}'.")
    if keep_artifacts:
        click.echo("Artifact cleanup skipped (--keep-artifacts).")
        return
    started = time.monotonic()
    try:
        while True:
            environments = eb.describe_environments(EnvironmentNames=[env_name], IncludeDeleted=True).get("Environments", [])
            if environments and all(item.get("Status") == "Terminated" for item in environments):
                break
            if time.monotonic() - started >= timeout:
                raise click.ClickException("Environment termination is still pending. Artifact cleanup was not run; "
                                           "charges may continue. Inspect environment events and retry destroy.")
            click.echo("Waiting for environment termination...")
            time.sleep(10)
        for message in cleanup_application_artifacts(session, app_name, deployment):
            click.echo(message)
        deleted = cleanup_environment_logs(session.client("logs"), env_name)
        click.echo(f"Deleted {len(deleted)} environment CloudWatch log/metric streams; shared log groups retained.")
        deployment["status"] = "Terminated"
        deployment["artifact_cleanup"] = "Completed eligible cleanup"
        save_deployment(deployment)
    except (BotoCoreError, ClientError, OSError, ValueError) as exc:
        raise click.ClickException(f"Termination requested, but artifact cleanup is incomplete: {exc}. "
                                   "Retained resources may still incur charges.") from exc
    click.echo("Environment terminated. EKS cleanup is managed by Elastic Beanstalk and may still be pending.")
    if deployment.get("cluster_arn"):
        stack_name = deployment["cluster_arn"].rsplit("/", 1)[-1]
        click.echo("After the three-hour reuse interval, verify managed infrastructure deletion with:")
        click.echo(f"  aws cloudformation wait stack-delete-complete --stack-name {stack_name} --region {selected_region}")


status = status_command
envlist = envlist_command
logs = logs_command
diagnose = diagnose_command
destroy = destroy_command
