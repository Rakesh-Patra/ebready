"""Deploy a public GitHub Docker application to Elastic Beanstalk Cluster Mode."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import boto3
import click
from botocore.exceptions import (
    BotoCoreError, ClientError, ParamValidationError, SSLError,
    ConnectionClosedError, EndpointConnectionError, ReadTimeoutError,
)

from ebkit.config import load_config
from ebkit.deployment_state import save_deployment, load_deployments
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


class ImageBuildError(RuntimeError):
    def __init__(self, message, project_name, build_id):
        super().__init__(message)
        self.build_info = {'codebuild_project': project_name, 'build_id': build_id}

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
        expected=os.environ.get("EBKIT_EXPECTED_AWS_ACCOUNT")
        if expected and identity.get("Account")!=expected:return False,None,"AWS profile account does not match this worker account."
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
            **({"PermissionsBoundary": os.environ["EBKIT_BUILD_PERMISSIONS_BOUNDARY"]} if service == "codebuild.amazonaws.com" and os.environ.get("EBKIT_BUILD_PERMISSIONS_BOUNDARY") else {}),
        )["Role"]

    boundary = os.environ.get("EBKIT_BUILD_PERMISSIONS_BOUNDARY") if service == "codebuild.amazonaws.com" else None
    if boundary and role.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn") != boundary:
        raise click.ClickException("Existing build role lacks the hosted permissions boundary; operator review is required.")

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
    configured = os.environ.get("EBKIT_PREPROVISIONED_ROLES")
    if configured:
        roles = json.loads(configured)
        required = {"cluster_role", "node_role", "observability_role"}
        if set(roles) != required or any(not re.fullmatch(r"arn:aws:iam::" + account_id + r":role/[A-Za-z0-9+=,.@_/-]+", value) for value in roles.values()):
            raise click.ClickException("Invalid operator-provisioned Cluster roles.")
        _, subnets = _get_default_vpc_subnets(ec2)
        return {**roles, "subnets": subnets}

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
    if parsed.scheme == 'zip':
        owner, repo = 'zip', app_name
    else:
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


def _codebuild_role_call(codebuild_client, operation: str, **arguments):
    """Retry only CodeBuild's service-role visibility error after IAM changes."""
    delays = (2, 4, 8, 16, 30, 30)
    for attempt in range(len(delays) + 1):
        try:
            return getattr(codebuild_client, operation)(**arguments)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            role_not_ready = (
                error.get("Code") == "InvalidInputException"
                and "sts:assumerole" in error.get("Message", "").lower()
            )
            if not role_not_ready:
                raise
            if attempt == len(delays):
                raise RuntimeError(
                    "CodeBuild still cannot assume its service role after waiting for IAM "
                    "propagation. Check the role ARN, trust for codebuild.amazonaws.com, "
                    "and regional STS availability. No build was confirmed by this request."
                ) from exc
            delay = delays[attempt]
            _info(f"Waiting {delay}s for CodeBuild service-role propagation "
                  f"(retry {attempt + 1}/{len(delays)})...")
            time.sleep(delay)


def _build_image_in_aws(
    iam_client,
    codebuild_client,
    account_id: str,
    region: str,
    app_name: str,
    repo_name: str,
    repo_url: str,
    image_uri: str,
    zip_source: Optional[Dict] = None,
    commit: Optional[str] = None,
    release_policy: Optional[Dict] = None,
) -> Dict[str, str]:
    parsed = urlsplit(repo_url)
    github_location = f"https://github.com{parsed.path.rstrip('/').removesuffix('.git')}.git"
    project_name, role_name = _codebuild_names(app_name, repo_url if zip_source else github_location)
    repository_arn = f"arn:aws:ecr:{region}:{account_id}:repository/{repo_name}"
    role_arn = _ensure_iam_role(iam_client, role_name, "codebuild.amazonaws.com", [])
    source_permissions = []
    if zip_source:
        source_permissions = [
            {'Effect':'Allow','Action':['s3:GetObject','s3:GetObjectVersion'],'Resource':f"arn:aws:s3:::{zip_source['bucket']}/{zip_source['key']}"},
            {'Effect':'Allow','Action':['s3:GetBucketLocation','s3:GetBucketAcl'],'Resource':f"arn:aws:s3:::{zip_source['bucket']}"},
        ]
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
            ] + source_permissions,
        }),
    )
    from ebkit.release import buildspec
    project_args = {
        "name": project_name,
        "description": f"Build public GitHub source for {app_name}",
        "source": {
            "type": "GITHUB",
            "location": github_location,
            "gitCloneDepth": 1,
            "buildspec": buildspec(release_policy),
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
    if zip_source:
        project_args['description'] = f'Build reviewed ZIP source for {app_name}'
        project_args['source'].update(type='S3', location=zip_source['location'])
        project_args['source'].pop('gitCloneDepth', None)
    try:
        _codebuild_role_call(codebuild_client, "create_project", **project_args)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceAlreadyExistsException":
            raise
        project_args.pop("tags", None)  # Do not claim ownership of a pre-existing project.
        _codebuild_role_call(codebuild_client, "update_project", **project_args)

    result = _codebuild_role_call(
        codebuild_client, "start_build",
        projectName=project_name,
        **({"sourceVersion": commit} if commit else {}),
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
    try:
        _wait_for_codebuild(codebuild_client, build_id)
    except (RuntimeError, TimeoutError) as error:
        raise ImageBuildError(str(error), project_name, build_id) from error
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
    infra: Dict, env_vars: Dict[str, str], port: int, max_replicas: int = 5
) -> List[Dict]:
    options = [
        {"Namespace": "aws:elasticbeanstalk:eks", "OptionName": "cluster-role", "Value": infra["cluster_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks", "OptionName": "node-role", "Value": infra["node_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "observability-role", "Value": infra["observability_role"]},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "service-port", "Value": str(port)},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "arch", "Value": "amd64"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "cpu", "Value": "250m"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "memory", "Value": "512Mi"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "memory-limit", "Value": "1Gi"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment", "OptionName": "load-balancer-type", "Value": "ALB"},
        {"Namespace": "aws:elasticbeanstalk:eks:alb", "OptionName": "scheme", "Value": "internet-facing"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "min-replica", "Value": "1"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "max-replica", "Value": str(max_replicas)},
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


def _existing_environment_update_options(eb_client, app_name, env_name, env_vars, port, max_replicas=None):
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
    return ([{"Namespace":"aws:elasticbeanstalk:eks:environment:autoscaling","OptionName":"min-replica","Value":"1"},{"Namespace":"aws:elasticbeanstalk:eks:environment:autoscaling","OptionName":"max-replica","Value":str(max_replicas)}] if max_replicas is not None else []) + [
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
    max_replicas: Optional[int] = None,
    require_existing: bool = False,
) -> bool:
    environments = eb_client.describe_environments(
        ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
    ).get("Environments", [])
    if environments:
        _info(f"Updating existing environment '{env_name}'.")
        return False
    if require_existing:
        raise RuntimeError("Watched environment disappeared; it will not be recreated.")
    eb_client.create_environment(
        ApplicationName=app_name,
        EnvironmentName=env_name,
        VersionLabel=version_label,
        Tier={"Name": "Cluster", "Type": "EKS"},
        OptionSettings=_build_cluster_option_settings(infra, env_vars, port, max_replicas or 5),
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
    connection_failures = 0
    while True:
        try:
            environments = eb_client.describe_environments(
                ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
            ).get("Environments", [])
            connection_failures = 0
        except (SSLError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError) as exc:
            connection_failures += 1
            if connection_failures >= 3 or time.monotonic() - started >= timeout:
                raise RuntimeError(
                    "AWS accepted the deployment, but its readiness could not be checked. "
                    "The environment may still be launching. Check Status before redeploying. "
                    + ("The deployment worker could not verify AWS's certificate; "
                       "the service operator must check the worker's trusted certificates."
                       if isinstance(exc, SSLError) else "The connection to AWS was interrupted.")
                ) from exc
            _info("Connection to AWS interrupted; retrying the readiness check in 15 seconds "
                  f"({connection_failures}/3).")
            time.sleep(15)
            continue
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
    port: Optional[int],
    wait: bool,
    env_file: Optional[str],
    extra_env: Tuple[str, ...],
    is_interactive: bool,
    archive_path: Optional[str] = None,
    max_replicas: Optional[int] = None,
    commit: Optional[str] = None,
    require_existing: bool = False,
    release_policy: Optional[Dict] = None,
    database_plan: Optional[Dict] = None,
) -> Dict:
    if database_plan:
        from ebkit.managed_database import validate_plan
        database_plan = validate_plan(database_plan)
        if not wait:raise click.ClickException("Managed database creation requires --wait.")
    if release_policy:
        from ebkit.release import validate_policy
        release_policy=validate_policy(release_policy)
        if not wait:raise click.ClickException("Release verification requires --wait.")
    if commit and (archive_path or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
        raise click.ClickException("--commit requires a full GitHub commit SHA and cannot be used with ZIPs.")
    config = load_config()
    parsed = urlsplit(source_url)
    repo_name = parsed.path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    authenticated, account_id, error = _check_aws_credentials()
    if not authenticated or not account_id:
        raise click.ClickException(f"AWS authentication required: {error}")
    records = load_deployments()
    canonical = lambda value: value.rstrip("/").removesuffix(".git").lower()
    if not region:
        saved_targets = [item for item in records if item.get("account_id") == account_id
                         and (not app or item.get("application_name") == app)
                         and (not env or item.get("environment_name") == env)
                         and (app or env or canonical(item.get("source_url", "")) == canonical(source_url))]
        if len(saved_targets) > 1:
            raise click.ClickException("Multiple saved targets match. Specify --region, --app and --environment.")
        if saved_targets:
            region = saved_targets[0].get("region")
    if not region:
        saved_region = getattr(config, "aws_region", None) or os.environ.get(
            "AWS_DEFAULT_REGION"
        )
        region = (
            _prompt_region(saved_region)
            if is_interactive
            else saved_region or "us-east-2"
        )


    # Match only this account and region. Explicit targets also support renamed ZIPs.
    canonical = lambda value: value.rstrip("/").removesuffix(".git").lower()
    matches = [item for item in records
               if item.get("account_id") == account_id and item.get("region") == region
               and (not app or item.get("application_name") == app)
               and (not env or item.get("environment_name") == env)
               and (app or env or canonical(item.get("source_url", "")) == canonical(source_url))]
    if len(matches) > 1:
        raise click.ClickException("Multiple saved targets match. Specify --app and --environment to select the deployment.")
    previous = matches[0] if matches else {}
    app = app or previous.get("application_name") or repo_name.lower().replace("_", "-")
    env = env or previous.get("environment_name") or f"{app}-cluster"
    port = port if port is not None else previous.get("port", 8080)
    if os.environ.get("EBKIT_HOSTED_REGION"):
        if region != os.environ["EBKIT_HOSTED_REGION"] or not re.fullmatch(r"ebk-[0-9a-f]{16}", app) or env != app + "-cluster":
            raise click.ClickException("Hosted deployments require an EBKit-generated target in the configured region.")
        max_replicas = 1


    env_vars = _collect_env_vars(env_file, extra_env, is_interactive)
    if database_plan and any(env_vars.get(name, '').strip() for name in database_plan['variables']):
        raise click.ClickException('Remove existing database URLs before choosing managed database creation.')
    image_uri = (
        f"{account_id}.dkr.ecr.{region}.amazonaws.com/{app}:v{time.time_ns()}"
    )
    codebuild_project, _ = _codebuild_names(app, source_url)
    deployment = {
        **previous,
        "account_id": account_id,
        "region": region,
        "application_name": app,
        "environment_name": env,
        "source_url": source_url,
        "port": port,
        "image_uri": image_uri,
        "source_commit": commit,
        "codebuild_project": codebuild_project,
        "status": "BUILDING",
    }
    if not require_existing:
        deployment.pop("watch_commit", None)
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
    zip_source = None
    if archive_path:
        from ebkit.zip_deploy import upload_source, source_bucket
        deployment['source_bucket'] = source_bucket(account_id, region, app)
        _persist_deployment(deployment)
        zip_source = upload_source(session.client('s3'), Path(archive_path), account_id, region, app)
        deployment['source_key'] = zip_source['key']
        _persist_deployment(deployment)
    ecr = session.client("ecr")
    eb = session.client("elasticbeanstalk")
    previous_version=None
    if release_policy:
        live=eb.describe_environments(ApplicationName=app,EnvironmentNames=[env],IncludeDeleted=False).get("Environments",[])
        if live:previous_version=live[0].get("VersionLabel")
    _ensure_ecr_repo(ecr, app)
    try:
      build_info = _build_image_in_aws(
        iam_client=session.client("iam"),
        codebuild_client=session.client("codebuild"),
        account_id=account_id,
        region=region,
        app_name=app,
        repo_name=app,
        repo_url=source_url,
        image_uri=image_uri,
        **({'zip_source':zip_source} if zip_source else {}),
        **({'commit':commit} if commit else {}),
        **({'release_policy':release_policy} if release_policy else {}),
    )
    except ImageBuildError as error:
        deployment.update(error.build_info)
        deployment['status'] = 'BUILD_FAILED'
        _persist_deployment(deployment)
        raise
    finally:
        if zip_source:
            try:
                session.client('s3').delete_object(Bucket=zip_source['bucket'], Key=zip_source['key'])
            except (BotoCoreError, ClientError):
                _info('Source ZIP cleanup is pending; destroy cleans owned source storage and a one-day lifecycle rule is configured.')
    deployment.update(build_info)
    deployment["codebuild_project"] = build_info.get("project_name", codebuild_project)
    _verify_ecr_image(ecr, image_uri)
    deployment["status"] = "BUILD_SUCCEEDED"
    _persist_deployment(deployment)

    database_subnets = None
    if database_plan:
        from ebkit.managed_database import prepare_database
        try:
            generated, database_subnets = prepare_database(session, app, env, deployment, database_plan)
            env_vars.update(generated)
        except Exception:
            deployment['status'] = 'DATABASE_SETUP_FAILED'
            _persist_deployment(deployment)
            raise click.ClickException('Managed database setup failed. Check network, IAM permissions and database status before retrying. Retained resources may incur charges; no credentials were printed.') from None
    infra = _provision_cluster_infrastructure(account_id, region)
    if database_subnets:infra["subnets"] = database_subnets
    _ensure_eb_application(eb, app)
    version_label = _make_application_version_label(image_uri)
    _register_cluster_version(eb, app, version_label, image_uri, region)
    deployment["version_label"] = version_label
    deployment["status"] = "DEPLOYING"
    _persist_deployment(deployment)
    created = _ensure_cluster_environment(
        eb, app, env, version_label, infra, env_vars, port, max_replicas,
        **({"require_existing": True} if require_existing else {}),
    )
    if not created:
        eb.update_environment(
            EnvironmentName=env,
            VersionLabel=version_label,
            OptionSettings=_existing_environment_update_options(eb, app, env, env_vars, port, max_replicas),
        )
        _ok(f"Deployment update started for '{env}'.")

    result = (
        _poll_environment_health(eb, app, env)
        if wait
        else {"status": "Deploying", "health": "Pending", "url": previous.get("url")}
    )
    if release_policy:
        from ebkit.release import verify_or_recover
        result.update(verify_or_recover(eb,app,env,version_label,previous_version,release_policy,_poll_environment_health))
    deployment.update(result)
    deployment["status"] = result["status"]
    deployment["health"] = result["health"]
    _persist_deployment(deployment)
    if release_policy and result.get("release_verification")!="passed":
        raise RuntimeError(f"Release verification failed; recovery: {result.get('recovery','unknown')}. Database migrations were not rolled back.")
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
@click.option("--port", default=None, type=click.IntRange(1, 65535), help="Container port (existing saved port, otherwise 8080)")
@click.option("--database-file", type=click.Path(exists=True,dir_okay=False), help="Reviewed managed PostgreSQL plan; creates or reuses a private database before deployment (additional charges).")
@click.option("--release-policy", type=click.Path(exists=True,dir_okay=False), help="Reviewed JSON test commands, HTTP checks and automatic recovery policy.")
@click.option("--commit", help="Build this full GitHub commit SHA instead of the branch head.")
@click.option("--max-replicas", type=click.IntRange(1,100), help="Set autoscaling maximum (new environments default to 5; updates preserve bounds unless set).")
@click.option("--wait/--no-wait", default=True, help="Wait for the environment to become ready")
@click.option("--env-file", help="Local file containing application environment variables")
@click.option("--env", "extra_env", multiple=True, metavar="KEY=VALUE", help="Application environment variable")
@click.option("-y", "--yes", is_flag=True, help="Skip the deployment confirmation")
@click.option('--reviewed', is_flag=True, help='Confirm that you reviewed the root Dockerfile in this ZIP.')
def deploy(source, app, environment_name, region, port, wait, env_file, extra_env, yes, reviewed=False, max_replicas=None, commit=None, release_policy=None, database_file=None):
    """Deploy a GitHub URL or a reviewed local project ZIP using AWS CodeBuild."""
    source = source.strip()
    database_plan = None
    if database_file:
        from ebkit.managed_database import validate_plan
        try:database_plan = validate_plan(json.loads(Path(database_file).read_text(encoding='utf-8')))
        except (OSError, ValueError):raise click.ClickException('Invalid managed database JSON file.') from None
    if release_policy:
        from ebkit.release import read_policy
        try:release_policy=read_policy(release_policy)
        except (ValueError,OSError) as error:raise click.ClickException(str(error)) from None
    if commit and (source.lower().endswith(".zip") or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
        raise click.ClickException("--commit requires a full GitHub commit SHA and cannot be used with ZIPs.")
    if Path(source).is_file() and source.lower().endswith('.zip'):
        from ebkit.source_bundle import extract_zip, make_zip
        import zipfile
        try:
            with tempfile.TemporaryDirectory(prefix='ebkit-zip-deploy-') as directory:
                root = extract_zip(Path(source), Path(directory)/'project')
                dockerfile = root/'Dockerfile'
                if not dockerfile.is_file() or not dockerfile.read_text(encoding='utf-8').strip():
                    raise click.ClickException('ZIP needs a root Dockerfile. Run ebkit init --zip first, then deploy the prepared ZIP.')
                if not reviewed:
                    click.echo('\nReview the root Dockerfile:\n'+dockerfile.read_text(encoding='utf-8'))
                    if yes or not sys.stdin.isatty():
                        raise click.ClickException('ZIP deployment requires --reviewed after checking its Dockerfile; --yes does not approve AI output.')
                    if not click.confirm('I reviewed this Dockerfile. Continue?',default=False): raise click.Abort()
                prepared = make_zip(root,Path(directory)/'source.zip')
                archive_name = re.sub(r'[^a-z0-9-]', '-', Path(source).stem.lower()).strip('-') or 'zip-app'
                _execute_deploy(source_url=f'zip://source/{archive_name}',app=app,env=environment_name,region=region,port=port,wait=wait,env_file=env_file,extra_env=extra_env,is_interactive=not yes and sys.stdin.isatty(),archive_path=str(prepared),**({'database_plan':database_plan} if database_plan else {}),**({'release_policy':release_policy} if release_policy else {}),**({'max_replicas':max_replicas} if max_replicas is not None else {}))
                return
        except (ValueError, OSError, zipfile.BadZipFile, BotoCoreError, ClientError, RuntimeError, TimeoutError) as error:
            if database_plan:raise click.ClickException('Deployment failed. Inspect AWS status and retained database resources; credentials were not printed.') from None
            raise click.ClickException(str(error)) from None
    valid, error = validate_github_url(source)
    if not valid:
        raise click.ClickException(
            f"SOURCE must be a public GitHub URL or local .zip file: {error}"
        )
    try:
        _execute_deploy(
            source_url=source,
            **({"database_plan":database_plan} if database_plan else {}),
            **({"release_policy":release_policy} if release_policy else {}),
            **({"commit":commit} if commit else {}),
            app=app,
            env=environment_name,
            region=region,
            port=port,
            wait=wait,
            env_file=env_file,
            extra_env=extra_env,
            is_interactive=not yes and sys.stdin.isatty(),
            **({'max_replicas':max_replicas} if max_replicas is not None else {}),
        )
    except (BotoCoreError, ClientError, RuntimeError, TimeoutError) as exc:
        if database_plan:raise click.ClickException('Deployment failed. Inspect AWS status and retained database resources; credentials were not printed.') from None
        raise click.ClickException(str(exc)) from exc


deploy_command = deploy

if __name__ == "__main__":
    deploy()
