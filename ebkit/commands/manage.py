"""Terminal controls for Beanstalk Cluster applications."""
from functools import wraps
import json

import click
from botocore.exceptions import BotoCoreError, ClientError

from ebkit.commands import operations as ops
from ebkit.commands.deploy import _collect_env_vars
from ebkit.aws_resources import environment_resources

ENV_NAMESPACE = "aws:elasticbeanstalk:eks:environment"
SCALE_NAMESPACE = ENV_NAMESPACE + ":autoscaling"


def target_options(function):
    for decorator in (
        click.option("--region", help="AWS region; defaults to saved configuration."),
        click.option("--environment", "--env-name", "environment_name", help="Target environment."),
        click.option("--app", help="Target application."),
    ):
        function = decorator(function)
    return function


def aws_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (BotoCoreError, ClientError, RuntimeError):
            # AWS error text can include configuration values. Never echo those.
            raise click.ClickException("AWS request failed. Check permissions and environment events; no settings were printed.") from None
    return wrapped


def target(app, environment_name, region):
    region = ops._region_option(region)
    session, account = ops._aws_session(region)
    app, environment, record = ops._resolve_deployment(session, account, region, app, environment_name)
    return session, app, environment, record


def settings(eb, app, environment):
    configs = eb.describe_configuration_settings(ApplicationName=app, EnvironmentName=environment).get("ConfigurationSettings", [])
    if not configs:
        raise click.ClickException("Environment configuration is unavailable.")
    return configs[0].get("OptionSettings", [])


def cluster_only(eb, environment):
    environments = eb.describe_environments(EnvironmentNames=[environment], IncludeDeleted=False).get("Environments", [])
    if not environments or environments[0].get("Tier", {}).get("Type") != "EKS":
        raise click.ClickException("This command requires a Beanstalk Cluster (EKS) environment.")
    if environments[0].get("Status") != "Ready":
        raise click.ClickException("Wait for the environment to become Ready before changing it.")


def confirm_change(environment, action, yes):
    click.echo(f"Target environment: {environment}. {action}")
    if not yes and not click.confirm("Apply this change?", default=False):
        raise click.Abort()


@click.command("scale")
@target_options
@click.option("--min", "minimum", required=True, type=click.IntRange(1, 100))
@click.option("--max", "maximum", required=True, type=click.IntRange(1, 100))
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@aws_errors
def scale_command(app, environment_name, region, minimum, maximum, yes):
    """Change Cluster replica bounds (at least one replica; this does not stop EKS charges)."""
    if minimum > maximum:
        raise click.UsageError("--min must not exceed --max.")
    session, app, environment, _ = target(app, environment_name, region)
    eb = session.client("elasticbeanstalk")
    cluster_only(eb, environment)
    confirm_change(environment, f"Set replica bounds to {minimum}-{maximum}; compute charges may change.", yes)
    eb.update_environment(EnvironmentName=environment, OptionSettings=[
        {"Namespace": SCALE_NAMESPACE, "OptionName": "min-replica", "Value": str(minimum)},
        {"Namespace": SCALE_NAMESPACE, "OptionName": "max-replica", "Value": str(maximum)},
    ])
    click.echo("Scaling update requested. Use ebkit status to check completion.")


@click.command("config")
@target_options
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False), help="Merge settings from a local file.")
@click.option("--env", "extra_env", multiple=True, metavar="KEY=VALUE", help="Set an application variable; repeat as needed.")
@click.option("--unset", multiple=True, metavar="KEY", help="Remove a variable; PORT cannot be removed.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@aws_errors
def config_command(app, environment_name, region, env_file, extra_env, unset, yes):
    """List variable names or update settings without rebuilding (values are never printed)."""
    session, app, environment, _ = target(app, environment_name, region)
    eb = session.client("elasticbeanstalk")
    options = settings(eb, app, environment)
    raw = next((item.get("Value", "{}") for item in options
                if item.get("Namespace") == ENV_NAMESPACE and item.get("OptionName") == "env-variables"), "{}")
    try:
        current = json.loads(raw)
        if not isinstance(current, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise click.ClickException("Stored application settings are invalid; no values were printed.") from None
    if not (env_file or extra_env or unset):
        click.echo(f"Application variables for {environment} (values hidden):")
        for key in sorted(current):
            click.echo(f"  {key}")
        return
    additions = _collect_env_vars(env_file, extra_env, False)
    if "PORT" in additions and str(additions["PORT"]) == str(current.get("PORT")):
        additions.pop("PORT")  # Accept the unchanged PORT from init's local .env.
    if "PORT" in additions or "PORT" in unset:
        raise click.UsageError("PORT is managed with the deployment's --port option.")
    if set(additions) & set(unset):
        raise click.UsageError("A variable cannot be both set and unset.")
    if any(not value.strip() for value in additions.values()):
        raise click.UsageError("Blank settings are not applied; fill them in or use --unset KEY.")
    cluster_only(eb, environment)
    confirm_change(environment, f"Set keys: {', '.join(sorted(additions)) or 'none'}; remove keys: {', '.join(unset) or 'none'}.", yes)
    current.update(additions)
    for key in unset:
        current.pop(key, None)
    eb.update_environment(EnvironmentName=environment, OptionSettings=[{
        "Namespace": ENV_NAMESPACE, "OptionName": "env-variables", "Value": json.dumps(current)
    }])
    click.echo("Configuration update requested; existing settings were preserved. Use ebkit status to check completion.")


@click.command("versions")
@target_options
@aws_errors
def versions_command(app, environment_name, region):
    """List application versions available for rollback."""
    session, app, environment, _ = target(app, environment_name, region)
    eb = session.client("elasticbeanstalk")
    token = None
    found = False
    while True:
        args = {"ApplicationName": app, "MaxRecords": 1000}
        if token:
            args["NextToken"] = token
        page = eb.describe_application_versions(**args)
        for version in page.get("ApplicationVersions", []):
            found = True
            click.echo(f"{version['VersionLabel']}  {version.get('DateCreated', '')}  {version.get('Status', '')}")
        token = page.get("NextToken")
        if not token:
            break
    if not found:
        click.echo("No application versions found.")


@click.command("rollback")
@target_options
@click.option("--version", required=True, help="An existing application version label from ebkit versions.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@aws_errors
def rollback_command(app, environment_name, region, version, yes):
    """Deploy an existing version; database changes and settings are not rolled back."""
    session, app, environment, _ = target(app, environment_name, region)
    eb = session.client("elasticbeanstalk")
    cluster_only(eb, environment)
    versions = eb.describe_application_versions(ApplicationName=app, VersionLabels=[version]).get("ApplicationVersions", [])
    if not versions:
        raise click.ClickException("Version not found. Run ebkit versions to choose an existing label.")
    confirm_change(environment, f"Deploy version {version}. Settings and database migrations remain unchanged; the image must still exist in ECR.", yes)
    eb.update_environment(EnvironmentName=environment, VersionLabel=version)
    click.echo("Rollback requested. Use ebkit status and ebkit logs to check completion.")


@click.command("cleanup-status")
@target_options
@aws_errors
def cleanup_status_command(app, environment_name, region):
    """Check recorded managed cluster deletion after destroy (read-only)."""
    selected_region = ops._region_option(region)
    session, account = ops._aws_session(selected_region)
    records = ops._matching_deployments(account, selected_region, app, environment_name)
    if not records:
        raise click.ClickException("No matching local deployment record; specify --app/--environment for a recorded deployment.")
    record = records[-1]
    click.echo(f"Environment: {record.get('environment_name')}")
    click.echo(f"Last recorded cleanup: {record.get('artifact_cleanup', 'Not recorded')}")
    cluster = record.get("cluster_arn")
    if not cluster:
        raise click.ClickException("No cluster ARN recorded. Destroy records it before termination.")
    name = cluster.rsplit("/", 1)[-1]
    try:
        stacks = session.client("cloudformation").describe_stacks(StackName=name).get("Stacks", [])
    except ClientError as exc:
        error = exc.response.get("Error", {})
        if error.get("Code") == "ValidationError" and "does not exist" in error.get("Message", ""):
            click.echo("Managed cluster stack deleted.")
            return
        raise
    state = stacks[0].get("StackStatus", "Unknown") if stacks else "Unknown"
    click.echo(f"Managed cluster stack: {name} [{state}]")
    if state != "DELETE_COMPLETE":
        click.echo("Cluster cleanup is not complete. Charges may continue; another environment may be using the cluster.")
        click.echo("Deletion is scheduled three hours after its last environment terminates. Inspect stack events if deletion fails.")


@click.command("resources")
@target_options
@click.option("--json", "as_json", is_flag=True, help="Print resource metadata as JSON.")
@aws_errors
def resources_command(app, environment_name, region, as_json):
    """Show live environment resources and recorded image/build identifiers (read-only)."""
    session, app, environment, record = target(app, environment_name, region)
    resources = environment_resources(session.client("elasticbeanstalk"), environment, ops._region_option(region))
    data = {
        "application": app, "environment": environment,
        "environment_resources": resources,
        "recorded_image_uri": record.get("image_uri"),
        "recorded_codebuild_project": record.get("codebuild_project"),
    }
    click.echo(json.dumps(data, indent=2, default=str))
    if not as_json:
        click.echo("Image/build identifiers come from local deployment history; resources may be shared. This is not a billing report.")
