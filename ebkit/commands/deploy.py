"""Deploy a public GitHub Docker application to Elastic Beanstalk Cluster Mode."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError

from ebkit.config import load_config
from ebkit.deployment_state import save_deployment
from ebkit.repo_handler import validate_github_url

_COMMON_REGIONS = [
    ("us-east-1", "US East (N. Virginia)"),
    ("us-east-2", "US East (Ohio)"),
    ("us-west-1", "US West (N. California)"),
    ("us-west-2", "US West (Oregon)"),
    ("eu-west-1", "Europe (Ireland)"),
    ("eu-central-1", "Europe (Frankfurt)"),
    ("ap-south-1", "Asia Pacific (Mumbai)"),
    ("ap-southeast-1", "Asia Pacific (Singapore)"),
    ("ap-northeast-1", "Asia Pacific (Tokyo)"),
]

_CLUSTER_ROLES = {
    "cluster": "aws-elasticbeanstalk-eks-cluster-role",
    "node": "aws-elasticbeanstalk-eks-node-role",
    "observability": "aws-elasticbeanstalk-eks-observability-role",
}

_CLUSTER_POLICIES = {
    "cluster": [
        "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        "arn:aws:iam::aws:policy/AWSElasticBeanstalkCustomPlatformforEC2Role",
    ],
    "node": [
        "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        "arn:aws:iam::aws:policy/AWSElasticBeanstalkMulticontainerDocker",
    ],
    "observability": [
        "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    ],
}


def _is_secret_environment_variable(name: str) -> bool:
    return bool(
        re.search(
            r"(PASS|PWD|SECRET|TOKEN|KEY|URI|URL|DSN|CONNECTION|CREDENTIAL)",
            name,
            re.IGNORECASE,
        )
    )


def _ok(message: str) -> None:
    click.echo(click.style(f"[+] {message}", fg="green"))


def _info(message: str) -> None:
    click.echo(f"[*] {message}")


def _table(rows: List[Dict], columns: List[str]) -> None:
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))
    click.echo(click.style("  ".join(c.ljust(widths[c]) for c in columns), bold=True))
    click.echo("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        click.echo("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))


def _check_aws_credentials() -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        identity = boto3.Session().client("sts").get_caller_identity()
        return True, identity.get("Account"), identity.get("Arn")
    except Exception as exc:
        return False, None, str(exc)


def _prompt_region(saved_region: Optional[str]) -> str:
    _info("Select an AWS region:")
    default_index = "2"
    for index, (code, description) in enumerate(_COMMON_REGIONS, 1):
        marker = " (current)" if code == saved_region else ""
        if code == saved_region:
            default_index = str(index)
        click.echo(f"  {index}) {code:<15} - {description}{marker}")
    click.echo(f"  {len(_COMMON_REGIONS) + 1}) Enter custom region")
    choice = click.prompt("Region", default=default_index)
    try:
        index = int(choice)
        if 1 <= index <= len(_COMMON_REGIONS):
            return _COMMON_REGIONS[index - 1][0]
    except ValueError:
        pass
    return click.prompt("AWS region name", default=saved_region or "us-east-2").strip()


def _load_env_file(env_file: Optional[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_file:
        return values
    path = Path(env_file)
    if not path.is_file():
        raise click.ClickException(f"Environment file not found: {path}")
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except OSError as exc:
        raise click.ClickException(f"Could not read environment file '{path}': {exc}") from exc
    return values


def _collect_env_vars(
    env_file: Optional[str],
    extra_env: Tuple[str, ...],
    is_interactive: bool,
) -> Dict[str, str]:
    merged = _load_env_file(env_file)
    for item in extra_env:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise click.ClickException("--env values must use KEY=VALUE format.")
        merged[key.strip()] = value.strip()

    if is_interactive:
        placeholders = [
            key for key, value in merged.items()
            if not value or value.lower() in ("changeme", "your-key", "xxx")
        ]
        for key in placeholders:
            value = click.prompt(
                f"Enter value for {key}",
                default="",
                show_default=False,
                hide_input=_is_secret_environment_variable(key),
            )
            if value:
                merged[key] = value

    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        merged.pop(key, None)
    return merged


def _ensure_iam_role(iam_client, role_name: str, service: str, policies: List[str]) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": service},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam_client.get_role(RoleName=role_name)["Role"]
    except iam_client.exceptions.NoSuchEntityException:
        _info(f"Creating IAM role '{role_name}'...")
        role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Created by EBKit for Elastic Beanstalk Cluster Mode",
        )["Role"]

    for policy_arn in policies:
        iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    return role["Arn"]


def _get_default_vpc_subnets(ec2_client) -> Tuple[Optional[str], List[str]]:
    vpcs = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"]
    if not vpcs:
        return None, []
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[
            {"Name": "vpcId", "Values": [vpc_id]},
            {"Name": "defaultForAz", "Values": ["true"]},
        ]
    )["Subnets"]
    return vpc_id, [subnet["SubnetId"] for subnet in subnets]


def _provision_cluster_infrastructure(account_id: str, region: str) -> Dict:
    _info("Checking the IAM roles and network required for Cluster Mode...")
    iam = boto3.client("iam")
    ec2 = boto3.client("ec2", region_name=region)
    roles = {
        name: _ensure_iam_role(
            iam, _CLUSTER_ROLES[name], service, _CLUSTER_POLICIES[name]
        )
        for name, service in (
            ("cluster", "eks.amazonaws.com"),
            ("node", "ec2.amazonaws.com"),
            ("observability", "ec2.amazonaws.com"),
        )
    }
    try:
        iam.create_service_linked_role(AWSServiceName="elasticbeanstalk.amazonaws.com")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidInput":
            raise

    _, subnets = _get_default_vpc_subnets(ec2)
    _ok("Cluster Mode infrastructure roles ready.")
    return {
        "cluster_role": roles["cluster"],
        "node_role": roles["node"],
        "observability_role": roles["observability"],
        "operation_role": (
            f"arn:aws:iam::{account_id}:role/aws-service-role/"
            "elasticbeanstalk.amazonaws.com/AWSServiceRoleForElasticBeanstalk"
        ),
        "subnets": subnets,
    }


def _ensure_ecr_repo(ecr_client, repo_name: str) -> str:
    try:
        ecr_client.create_repository(repositoryName=repo_name, tags=[
            {"Key": "ManagedBy", "Value": "EBKit"},
            {"Key": "EBKitApplication", "Value": repo_name},
        ])
        _ok(f"ECR repository '{repo_name}' created.")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "RepositoryAlreadyExistsException":
            raise
    response = ecr_client.describe_repositories(repositoryNames=[repo_name])
    return response["repositories"][0]["repositoryUri"]


def _verify_ecr_image(ecr_client, image_uri: str) -> None:
    image_reference = image_uri.rsplit("/", 1)[-1]
    repository_name, separator, image_tag = image_reference.rpartition(":")
    if not separator or not repository_name or not image_tag:
        raise RuntimeError(f"Could not determine ECR image repository and tag from '{image_uri}'.")
    try:
        images = ecr_client.describe_images(
            repositoryName=repository_name,
            imageIds=[{"imageTag": image_tag}],
        ).get("imageDetails", [])
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Could not verify ECR image '{image_uri}': {exc}") from exc
    if not images or not images[0].get("imageDigest"):
        raise RuntimeError(
            f"CodeBuild completed but ECR does not contain image '{image_uri}'."
        )


def _codebuild_names(app_name: str, repo_url: str) -> Tuple[str, str]:
    parsed = urlsplit(repo_url)
    owner, repo = parsed.path.strip("/").removesuffix(".git").split("/")
    full_name = f"{app_name}-{owner}-{repo}"
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", full_name).strip("-") or "app"
    suffix = f"{slug[:36].rstrip('-')}-{hashlib.sha256(full_name.encode()).hexdigest()[:8]}"
    return f"ebkit-{suffix}-image-build", f"ebkit-{suffix}-build-role"


def _persist_deployment(deployment: Dict) -> None:
    try:
        save_deployment(deployment)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not save deployment state: {exc}") from exc


def _wait_for_codebuild(codebuild_client, build_id: str, timeout: int = 3600) -> None:
    _info(f"Waiting for AWS CodeBuild build '{build_id}'...")
    started = time.monotonic()
    while True:
        builds = codebuild_client.batch_get_builds(ids=[build_id]).get("builds", [])
        if not builds:
            raise RuntimeError(f"AWS CodeBuild could not find build '{build_id}'.")
        build = builds[0]
        status = build.get("buildStatus")
        if status == "SUCCEEDED":
            _ok("AWS CodeBuild built and pushed the image.")
            return
        if status in {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}:
            logs_url = build.get("logs", {}).get("deepLink")
            detail = f" Status: {status}."
            if logs_url:
                detail += f" Build logs: {logs_url}"
            raise RuntimeError(f"AWS CodeBuild failed.{detail}")
        if time.monotonic() - started >= timeout:
            raise TimeoutError(
                f"AWS CodeBuild did not finish within {timeout} seconds. Build ID: {build_id}"
            )
        time.sleep(10)


def _build_image_in_aws(
    iam_client,
    codebuild_client,
    account_id: str,
    region: str,
    app_name: str,
    repo_name: str,
    repo_url: str,
    image_uri: str,
) -> Dict[str, str]:
    parsed = urlsplit(repo_url)
    github_location = f"https://github.com{parsed.path.rstrip('/').removesuffix('.git')}.git"
    project_name, role_name = _codebuild_names(app_name, github_location)
    repository_arn = f"arn:aws:ecr:{region}:{account_id}:repository/{repo_name}"
    role_arn = _ensure_iam_role(iam_client, role_name, "codebuild.amazonaws.com", [])
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="EBKitBuildAndPushImage",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:CompleteLayerUpload",
                        "ecr:InitiateLayerUpload",
                        "ecr:PutImage",
                        "ecr:UploadLayerPart",
                    ],
                    "Resource": repository_arn,
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": [
                        f"arn:aws:logs:{region}:{account_id}:log-group:/aws/codebuild/{project_name}",
                        f"arn:aws:logs:{region}:{account_id}:log-group:/aws/codebuild/{project_name}:*",
                    ],
                },
            ],
        }),
    )
    project_args = {
        "name": project_name,
        "description": f"Build public GitHub source for {app_name}",
        "source": {
            "type": "GITHUB",
            "location": github_location,
            "gitCloneDepth": 1,
            "buildspec": (
                "version: 0.2\n"
                "phases:\n"
                "  pre_build:\n"
                "    commands:\n"
                "      - aws ecr get-login-password --region \"$AWS_DEFAULT_REGION\" "
                "| docker login --username AWS --password-stdin \"$EBKIT_ECR_REGISTRY\"\n"
                "  build:\n"
                "    commands:\n"
                "      - docker build --platform linux/amd64 -t \"$EBKIT_IMAGE_URI\" .\n"
                "      - docker push \"$EBKIT_IMAGE_URI\"\n"
            ),
        },
        "artifacts": {"type": "NO_ARTIFACTS"},
        "environment": {
            "type": "LINUX_CONTAINER",
            "image": "aws/codebuild/standard:7.0",
            "computeType": "BUILD_GENERAL1_SMALL",
            "privilegedMode": True,
        },
        "serviceRole": role_arn,
        "timeoutInMinutes": 60,
        "tags": [{"key": "ManagedBy", "value": "EBKit"},
                 {"key": "EBKitApplication", "value": app_name}],
    }
    try:
        codebuild_client.create_project(**project_args)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceAlreadyExistsException":
            raise
        project_args.pop("tags", None)  # Do not claim ownership of a pre-existing project.
        codebuild_client.update_project(**project_args)

    result = codebuild_client.start_build(
        projectName=project_name,
        environmentVariablesOverride=[
            {
                "name": "EBKIT_ECR_REGISTRY",
                "value": image_uri.split("/", 1)[0],
                "type": "PLAINTEXT",
            },
            {"name": "EBKIT_IMAGE_URI", "value": image_uri, "type": "PLAINTEXT"},
        ],
    )
    build_id = result["build"]["id"]
    _wait_for_codebuild(codebuild_client, build_id)
    return {"project_name": project_name, "build_id": build_id}


def _ensure_eb_application(eb_client, app_name: str) -> None:
    apps = eb_client.describe_applications(ApplicationNames=[app_name]).get(
        "Applications", []
    )
    if not apps:
        _info(f"Creating Elastic Beanstalk application '{app_name}'...")
        eb_client.create_application(
            ApplicationName=app_name, Description="Managed by EBKit"
        )


def _build_cluster_option_settings(
    infra: Dict, env_vars: Dict[str, str], port: int
) -> List[Dict]:
    options = [
        {"Namespace": "aws:elasticbeanstalk:eks", "OptionName": "cluster-role", "Value": infra["cluster_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks", "OptionName": "node-role", "Value": infra["node_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "operation-role", "Value": infra["operation_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "observability-role", "Value": infra["observability_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "service-port", "Value": str(port)},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "arch", "Value": "amd64"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "cpu", "Value": "250m"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "memory", "Value": "512Mi"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "memory-limit", "Value": "1Gi"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "load-balancer-type", "Value": "ALB"},
        {"Namespace": "aws:elasticbeanstalk:eks:alb", "OptionName": "scheme", "Value": "internet-facing"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "min-replica", "Value": "1"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "max-replica", "Value": "5"},
    ]
    if infra.get("subnets"):
        options.append({
            "Namespace": "aws:elasticbeanstalk:eks:environment",
            "OptionName": "subnets",
            "Value": ",".join(infra["subnets"]),
        })
    environment = dict(env_vars)
    environment["PORT"] = str(port)
    options.append({
        "Namespace": "aws:elasticbeanstalk:eks:environment",
        "OptionName": "env-variables",
        "Value": json.dumps(environment),
    })
    return options


def _existing_environment_update_options(eb_client, app_name, env_name, env_vars, port):
    """Preserve existing settings and replica bounds when deploying a new image."""
    configurations = eb_client.describe_configuration_settings(
        ApplicationName=app_name, EnvironmentName=env_name
    ).get("ConfigurationSettings", [])
    if not configurations:
        raise RuntimeError("Could not read existing environment settings; deployment update was not applied.")
    existing = {}
    for option in configurations[0].get("OptionSettings", []):
        if option.get("Namespace") == "aws:elasticbeanstalk:eks:environment" and option.get("OptionName") == "env-variables":
            try:
                existing = json.loads(option.get("Value", "{}"))
                if not isinstance(existing, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                raise RuntimeError("Existing environment settings are invalid; no values were printed.") from None
    existing.update(env_vars)
    existing["PORT"] = str(port)
    return [
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "service-port", "Value": str(port)},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "env-variables", "Value": json.dumps(existing)},
    ]


def _ensure_cluster_environment(
    eb_client,
    app_name: str,
    env_name: str,
    version_label: str,
    infra: Dict,
    env_vars: Dict[str, str],
    port: int,
) -> bool:
    environments = eb_client.describe_environments(
        ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
    ).get("Environments", [])
    if environments:
        _info(f"Updating existing environment '{env_name}'.")
        return False
    eb_client.create_environment(
        ApplicationName=app_name,
        EnvironmentName=env_name,
        VersionLabel=version_label,
        Tier={"Name": "Cluster", "Type": "EKS"},
        OptionSettings=_build_cluster_option_settings(infra, env_vars, port),
    )
    _ok(f"Cluster environment '{env_name}' launched.")
    return True


def _register_cluster_version(
    eb_client, app_name: str, version_label: str, image_uri: str, region: str
) -> None:
    version_args = {
        "ApplicationName": app_name,
        "VersionLabel": version_label,
        "Description": f"EBKit Cluster deployment: {image_uri}",
        "ImageConfiguration": {"Source": {"Uri": image_uri}},
        "AutoCreateApplication": True,
    }
    try:
        eb_client.create_application_version(**version_args)
    except ParamValidationError:
        command = [
            "aws",
            "elasticbeanstalk",
            "create-application-version",
            "--application-name",
            app_name,
            "--version-label",
            version_label,
            "--description",
            version_args["Description"],
            "--image-configuration",
            json.dumps(version_args["ImageConfiguration"]),
            "--region",
            region,
            "--no-cli-pager",
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode:
            error = result.stderr.strip() or result.stdout.strip()
            if "already exists" in error.lower():
                return
            raise RuntimeError(f"AWS CLI could not register application version: {error}")
    except ClientError as exc:
        if "already exists" in str(exc).lower():
            return
        raise
    _ok(f"Application version '{version_label}' registered.")


def _make_application_version_label(image_uri: str) -> str:
    version_tag = image_uri.rsplit(":", 1)[-1]
    suffix = str(time.time_ns())
    return f"{version_tag[:99 - len(suffix)]}-{suffix}"


def _poll_environment_health(
    eb_client, app_name: str, env_name: str, timeout: int = 1800
) -> Dict:
    _info(f"Waiting for '{env_name}' to become ready...")
    started = time.monotonic()
    while True:
        environments = eb_client.describe_environments(
            ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
        ).get("Environments", [])
        if environments:
            environment = environments[0]
            status = environment.get("Status", "Unknown")
            health = environment.get("Health", "Unknown")
            cname = environment.get("CNAME")
            click.echo(f"    Status={status} | Health={health}")
            if status == "Ready" and str(health).lower() != "grey":
                return {
                    "status": status,
                    "health": health,
                    "url": f"https://{cname}" if cname else None,
                }
            if status in ("Terminated", "Terminating"):
                return {"status": status, "health": health, "url": None}
        if time.monotonic() - started >= timeout:
            return {"status": "Timeout", "health": "Unknown", "url": None}
        time.sleep(15)


def _execute_deploy(
    source_url: str,
    app: Optional[str],
    env: Optional[str],
    region: Optional[str],
    port: int,
    wait: bool,
    env_file: Optional[str],
    extra_env: Tuple[str, ...],
    is_interactive: bool,
) -> Dict:
    config = load_config()
    parsed = urlsplit(source_url)
    repo_name = parsed.path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if not app:
        app = repo_name.lower().replace("_", "-")
    if not env:
        env = f"{app}-cluster"
    if not region:
        saved_region = getattr(config, "aws_region", None) or os.environ.get(
            "AWS_DEFAULT_REGION"
        )
        region = (
            _prompt_region(saved_region)
            if is_interactive
            else saved_region or "us-east-2"
        )

    authenticated, account_id, error = _check_aws_credentials()
    if not authenticated or not account_id:
        raise click.ClickException(f"AWS authentication required: {error}")

    env_vars = _collect_env_vars(env_file, extra_env, is_interactive)
    image_uri = (
        f"{account_id}.dkr.ecr.{region}.amazonaws.com/{app}:v{int(time.time())}"
    )
    codebuild_project, _ = _codebuild_names(app, source_url)
    deployment = {
        "account_id": account_id,
        "region": region,
        "application_name": app,
        "environment_name": env,
        "source_url": source_url,
        "port": port,
        "image_uri": image_uri,
        "codebuild_project": codebuild_project,
        "status": "BUILDING",
    }
    if is_interactive:
        _table(
            [
                {"Setting": "GitHub source", "Value": source_url},
                {"Setting": "Region", "Value": region},
                {"Setting": "Application", "Value": app},
                {"Setting": "Environment", "Value": env},
                {"Setting": "Service port", "Value": str(port)},
            ],
            ["Setting", "Value"],
        )
        if not click.confirm("Deploy to Elastic Beanstalk Cluster Mode?", default=True):
            raise click.Abort()

    _persist_deployment(deployment)
    session = boto3.Session(region_name=region)
    ecr = session.client("ecr")
    eb = session.client("elasticbeanstalk")
    _ensure_ecr_repo(ecr, app)
    build_info = _build_image_in_aws(
        iam_client=session.client("iam"),
        codebuild_client=session.client("codebuild"),
        account_id=account_id,
        region=region,
        app_name=app,
        repo_name=app,
        repo_url=source_url,
        image_uri=image_uri,
    )
    deployment.update(build_info)
    deployment["codebuild_project"] = build_info.get("project_name", codebuild_project)
    _verify_ecr_image(ecr, image_uri)
    deployment["status"] = "BUILD_SUCCEEDED"
    _persist_deployment(deployment)

    infra = _provision_cluster_infrastructure(account_id, region)
    _ensure_eb_application(eb, app)
    version_label = _make_application_version_label(image_uri)
    _register_cluster_version(eb, app, version_label, image_uri, region)
    deployment["version_label"] = version_label
    deployment["status"] = "DEPLOYING"
    _persist_deployment(deployment)
    created = _ensure_cluster_environment(
        eb, app, env, version_label, infra, env_vars, port
    )
    if not created:
        eb.update_environment(
            EnvironmentName=env,
            VersionLabel=version_label,
            OptionSettings=_existing_environment_update_options(eb, app, env, env_vars, port),
        )
        _ok(f"Deployment update started for '{env}'.")

    result = (
        _poll_environment_health(eb, app, env)
        if wait
        else {"status": "Deploying", "health": "Pending", "url": None}
    )
    deployment.update(result)
    deployment["status"] = result["status"]
    deployment["health"] = result["health"]
    _persist_deployment(deployment)
    if wait and result["status"] != "Ready":
        raise RuntimeError(
            f"Elastic Beanstalk environment '{env}' did not become ready "
            f"(status: {result['status']}, health: {result['health']})."
        )
    click.echo("")
    click.echo(f"Deployment: {result['status']} | Health: {result['health']}")
    if result.get("url"):
        click.echo(f"Live application: {result['url']}")
    else:
        click.echo(
            f"Elastic Beanstalk console: https://console.aws.amazon.com/"
            f"elasticbeanstalk/home?region={region}"
        )
    return result


@click.command()
@click.argument("source")
@click.option("--app", help="Elastic Beanstalk application name (defaults to repository name)")
@click.option("--environment", "--env-name", "environment_name", help="Elastic Beanstalk environment name")
@click.option("--region", help="AWS region (for example, us-east-2)")
@click.option("--port", default=8080, show_default=True, type=click.IntRange(1, 65535), help="Port the container listens on")
@click.option("--wait/--no-wait", default=True, help="Wait for the environment to become ready")
@click.option("--env-file", help="Local file containing application environment variables")
@click.option("--env", "extra_env", multiple=True, metavar="KEY=VALUE", help="Application environment variable")
@click.option("-y", "--yes", is_flag=True, help="Skip the deployment confirmation")
def deploy(source, app, environment_name, region, port, wait, env_file, extra_env, yes):
    """Deploy a public GitHub repository containing a root Dockerfile."""
    source = source.strip()
    valid, error = validate_github_url(source)
    if not valid:
        raise click.ClickException(
            f"SOURCE must be a public GitHub repository URL: {error}"
        )
    try:
        _execute_deploy(
            source_url=source,
            app=app,
            env=environment_name,
            region=region,
            port=port,
            wait=wait,
            env_file=env_file,
            extra_env=extra_env,
            is_interactive=not yes and sys.stdin.isatty(),
        )
    except (BotoCoreError, ClientError, RuntimeError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc


deploy_command = deploy

if __name__ == "__main__":
    deploy()
