"""
ebkit deploy - Build and deploy Docker containers to AWS Elastic Beanstalk Cluster Mode.

Features:
  - GitHub URL or local path input (ebkit deploy <github-url> or ebkit deploy .)
  - Fully automated AWS Elastic Beanstalk Cluster Mode provisioning (Zero infra management)
  - Architecture detection (single-tier, multi-tier / frontend, backend, db, cache, worker)
  - Interactive & automated environment variables (.env, --env-file, --env KEY=VAL)
  - Health verification polling and structured table summary
  - Interactive UX wizard with non-interactive CI/CD (-y / --yes) support
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import boto3
import click
import yaml
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from ebkit.config import load_config
from ebkit.repo_handler import safe_clone_repo, validate_github_url

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

_TIER_HINTS = {
    "frontend": ["frontend", "web", "ui", "static", "nginx", "react", "vue", "angular", "next"],
    "backend": ["backend", "api", "server", "app", "django", "flask", "fastapi", "rails", "express"],
    "database": ["db", "database", "postgres", "mysql", "mongo", "sqlite"],
    "worker": ["worker", "celery", "rq", "queue", "sidekiq", "beat"],
    "cache": ["cache", "redis", "memcached"],
}

_STATEFUL_IMAGES = {
    "postgres": ("database", "PostgreSQL"),
    "postgresql": ("database", "PostgreSQL"),
    "mysql": ("database", "MySQL"),
    "mariadb": ("database", "MariaDB"),
    "mongo": ("database", "MongoDB"),
    "mongodb": ("database", "MongoDB"),
    "redis": ("cache", "Redis"),
    "valkey": ("cache", "Redis-compatible"),
    "memcached": ("cache", "Memcached"),
}

_DATABASE_ENV_KEY = re.compile(
    r"(DATABASE|DB_|_DB$|POSTGRES|MYSQL|MONGO|REDIS|CACHE|SQL|_URI$|_URL$|_DSN$|CONNECTION)",
    re.IGNORECASE,
)
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _is_secret_environment_variable(name: str) -> bool:
    return bool(
        re.search(
            r"(PASS|PWD|SECRET|TOKEN|KEY|URI|URL|DSN|CONNECTION|CREDENTIAL)",
            name,
            re.IGNORECASE,
        )
    )


def _banner() -> None:
    click.echo("")
    click.echo(click.style("=" * 64, fg="cyan"))
    click.echo(click.style("   EBReady — AWS Elastic Beanstalk Cluster Mode Deployer", fg="bright_cyan", bold=True))
    click.echo(click.style("   Zero-infra management: Auto-provisions cluster & deploys app", fg="white"))
    click.echo(click.style("=" * 64, fg="cyan"))
    click.echo("")


def _ok(msg: str) -> None:
    click.echo(click.style(f"[+] {msg}", fg="green"))


def _info(msg: str) -> None:
    click.echo(f"[*] {msg}")


def _warn(msg: str) -> None:
    click.echo(click.style(f"[!] {msg}", fg="yellow"))


def _err(msg: str) -> None:
    click.echo(click.style(f"[X] {msg}", fg="red"), err=True)


def _section(title: str) -> None:
    click.echo(click.style(f"\n--- {title} ---", bold=True))


def _table(rows: List[Dict], columns: List[str]) -> None:
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    click.echo(click.style(header, bold=True))
    click.echo(sep)
    for row in rows:
        health = str(row.get("Health", "")).lower()
        if any(h in health for h in ["green", "ok", "ready"]):
            color = "green"
        elif any(h in health for h in ["yellow", "pending", "launching"]):
            color = "yellow"
        elif any(h in health for h in ["red", "degraded", "severe"]):
            color = "red"
        else:
            color = "white"
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns)
        click.echo(click.style(line, fg=color))


def _check_aws_credentials() -> Tuple[bool, Optional[str], Optional[str]]:
    try:
        session = boto3.Session()
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return True, identity.get("Account"), identity.get("Arn")
    except (NoCredentialsError, ClientError, Exception) as exc:
        return False, None, str(exc)


def _guide_aws_credentials(region: str) -> None:
    _warn("AWS credentials not detected or invalid.")
    click.echo("  1) Run 'aws configure' (interactive AWS CLI setup)")
    click.echo("  2) Paste Access Key & Secret Key for this session")
    click.echo("  3) Re-check credentials")
    click.echo("  4) Abort")
    choice = click.prompt("Select option", type=click.Choice(["1", "2", "3", "4"]), default="1")
    if choice == "1":
        subprocess.run(["aws", "configure"], check=False)
    elif choice == "2":
        ak = click.prompt("AWS Access Key ID")
        sk = click.prompt("AWS Secret Access Key", hide_input=True)
        st = click.prompt("AWS Session Token (Enter to skip)", default="", show_default=False)
        os.environ["AWS_ACCESS_KEY_ID"] = ak.strip()
        os.environ["AWS_SECRET_ACCESS_KEY"] = sk.strip()
        if st.strip():
            os.environ["AWS_SESSION_TOKEN"] = st.strip()
        os.environ["AWS_DEFAULT_REGION"] = region
    elif choice == "3":
        pass
    else:
        click.echo("Aborted.")
        sys.exit(0)
    ok, acc, err = _check_aws_credentials()
    if not ok:
        _err(f"Credentials still unavailable: {err}")
        sys.exit(1)
    _ok(f"AWS authenticated — Account: {acc}")


def _detect_architecture(proj_dir: Path) -> Dict:
    compose_files = list(proj_dir.glob("docker-compose*.y*ml")) + list(proj_dir.glob("compose*.y*ml"))
    services = []
    tier = "single-tier"

    if compose_files:
        tier = "multi-tier"
        try:
            content = compose_files[0].read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            in_services = False
            for line in lines:
                stripped = line.strip()
                if stripped == "services:":
                    in_services = True
                    continue
                if in_services and line and not line.startswith(" ") and not line.startswith("\t"):
                    in_services = False
                if in_services and line.startswith("  ") and ":" in line and not line.startswith("   "):
                    svc_name = stripped.rstrip(":").strip()
                    if svc_name:
                        services.append(svc_name)
        except Exception:
            pass

    if not services:
        subdirs = [p.name for p in proj_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and (p / "Dockerfile").exists()]
        if len(subdirs) > 1:
            tier = "multi-tier"
            services = subdirs
        else:
            services = [proj_dir.name]

    service_roles = []
    for svc in services:
        svc_lower = svc.lower()
        role = "backend"
        for tier_name, hints in _TIER_HINTS.items():
            if any(h in svc_lower for h in hints):
                role = tier_name
                break
        service_roles.append({"name": svc, "role": role})

    return {"tier": tier, "services": service_roles}


def _load_compose_web_services(project_dir: Path) -> Tuple[List[Dict], Tuple[str, ...]]:
    compose_files = [
        project_dir / name
        for name in (
            "compose.yaml",
            "compose.yml",
            "docker-compose.yaml",
            "docker-compose.yml",
        )
        if (project_dir / name).is_file()
    ]
    if not compose_files:
        raise click.ClickException(
            "--compose requires a compose.yaml, compose.yml, docker-compose.yaml, "
            "or docker-compose.yml file in the project root."
        )

    compose_path = compose_files[0]
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(f"Could not read Compose file '{compose_path}': {exc}") from exc

    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        raise click.ClickException(f"Compose file '{compose_path}' must define services.")

    parsed_services: Dict[str, Dict] = {}
    unsupported = []
    for name, config in services.items():
        if not isinstance(config, dict):
            raise click.ClickException(f"Compose service '{name}' must be a mapping.")
        service_name = str(name)
        role = next(
            (
                role_name
                for role_name, hints in _TIER_HINTS.items()
                if any(hint in service_name.lower() for hint in hints)
            ),
            "backend",
        )
        image = str(config.get("image", "")).lower()
        image_name = image.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
        stateful = _STATEFUL_IMAGES.get(image_name)
        if stateful:
            role = stateful[0]
        build = config.get("build")
        if not build:
            if role in ("database", "cache"):
                parsed_services[service_name] = {
                    "name": service_name,
                    "config": config,
                    "external": True,
                    "role": role,
                    "database_type": stateful[1] if stateful else None,
                }
            else:
                unsupported.append(service_name)
            continue
        if isinstance(build, str):
            context = (compose_path.parent / build).resolve()
            dockerfile = context / "Dockerfile"
        elif isinstance(build, dict):
            if "dockerfile_inline" in build:
                raise click.ClickException(
                    f"Compose service '{service_name}' uses dockerfile_inline, which EBKit "
                    "cannot stage. Add a Dockerfile to the build context instead."
                )
            context = (compose_path.parent / str(build.get("context", "."))).resolve()
            dockerfile = (context / str(build.get("dockerfile", "Dockerfile"))).resolve()
        else:
            raise click.ClickException(f"Compose service '{name}' has an invalid build setting.")
        if not context.is_dir() or not dockerfile.is_file():
            raise click.ClickException(
                f"Compose service '{name}' must build from a directory containing a Dockerfile: "
                f"{context}"
            )
        if role in ("database", "cache", "worker"):
            raise click.ClickException(
                f"Compose service '{service_name}' is classified as a {role}. EB Cluster "
                "deploys HTTP services; use a managed database/cache or a worker platform "
                "designed for background jobs."
            )
        try:
            dockerfile.relative_to(context)
        except ValueError as exc:
            raise click.ClickException(
                f"Compose service '{name}' Dockerfile must be inside its build context."
            ) from exc
        if config.get("volumes"):
            raise click.ClickException(
                f"Compose service '{service_name}' mounts volumes. EB Cluster deployment does "
                "not translate container mounts; move persistent data to a managed service or "
                "remove the mount."
            )
        if config.get("network_mode") or config.get("devices") or config.get("privileged"):
            raise click.ClickException(
                f"Compose service '{service_name}' uses host-level networking or privileges, "
                "which cannot be represented in an EB Cluster service."
            )
        for port_mapping in config.get("ports", []):
            protocol = (
                port_mapping.get("protocol", "tcp")
                if isinstance(port_mapping, dict)
                else str(port_mapping).rsplit("/", 1)[-1]
                if "/" in str(port_mapping)
                else "tcp"
            )
            if protocol.lower() != "tcp":
                raise click.ClickException(
                    f"Compose service '{service_name}' publishes a non-TCP port. "
                    "Elastic Beanstalk Cluster environments expose HTTP services, not UDP."
                )
        unsupported_options = (
            "command",
            "entrypoint",
            "healthcheck",
            "secrets",
            "configs",
            "deploy",
            "pid",
            "ipc",
            "runtime",
            "cap_add",
            "cap_drop",
            "security_opt",
            "restart",
            "links",
            "extra_hosts",
        )
        present_unsupported = [key for key in unsupported_options if key in config]
        if present_unsupported:
            raise click.ClickException(
                f"Compose service '{service_name}' uses options EBKit cannot translate to "
                "Elastic Beanstalk Cluster Mode: " + ", ".join(present_unsupported)
            )
        parsed_services[service_name] = {
            "name": service_name,
            "config": config,
            "context": context,
            "role": role,
            "database_type": None,
            "dockerfile": dockerfile,
            "build": build,
            "external": False,
        }

    if unsupported:
        raise click.ClickException(
            "Compose deployment needs each deployable service to define a Docker build. "
            "Services without a build must be external databases or caches. Unsupported "
            "services: " + ", ".join(unsupported)
        )

    visiting = set()
    visited = set()
    ordered = []

    def stateful_dependencies(service_name: str, seen=None):
        seen = set() if seen is None else seen
        if service_name in seen:
            return []
        seen.add(service_name)
        result = []
        dependencies = parsed_services[service_name]["config"].get("depends_on", [])
        if isinstance(dependencies, dict):
            dependencies = list(dependencies)
        for dependency in dependencies or []:
            depended_service = parsed_services[dependency]
            if depended_service["external"]:
                if depended_service["role"] in ("database", "cache"):
                    result.append(depended_service)
            else:
                result.extend(stateful_dependencies(dependency, seen))
        return result

    def visit(service_name: str) -> None:
        if service_name in visiting:
            raise click.ClickException(f"Compose service dependency cycle detected at '{service_name}'.")
        if service_name in visited:
            return
        visiting.add(service_name)
        dependencies = parsed_services[service_name]["config"].get("depends_on", [])
        if isinstance(dependencies, dict):
            dependencies = list(dependencies)
        for dependency in dependencies or []:
            if dependency not in parsed_services:
                raise click.ClickException(
                    f"Compose service '{service_name}' depends on undefined service '{dependency}'."
                )
            visit(dependency)
        visiting.remove(service_name)
        visited.add(service_name)
        if not parsed_services[service_name]["external"]:
            parsed_services[service_name]["stateful_dependencies"] = stateful_dependencies(
                service_name
            )
            ordered.append(parsed_services[service_name])

    for service_name in parsed_services:
        visit(service_name)
    if not ordered:
        raise click.ClickException("Compose file has no Docker-built HTTP services to deploy.")
    return ordered, tuple(
        name for name, service in parsed_services.items() if service["external"]
    )


def _detect_service_port(service: Dict) -> int:
    for item in service["config"].get("expose", []):
        value = item.get("target") if isinstance(item, dict) else str(item).split("/")[-1]
        value = str(value)
        if value.isdigit():
            return int(value)
    for item in service["config"].get("ports", []):
        value = item.get("target") if isinstance(item, dict) else str(item).split(":")[-1].split("/")[0]
        value = str(value)
        if value.isdigit():
            return int(value)
    try:
        for line in service["dockerfile"].read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().upper().startswith("EXPOSE"):
                port = line.split()[1].split("/")[0]
                if port.isdigit():
                    return int(port)
    except (OSError, IndexError):
        pass
    raise click.ClickException(
        f"Could not determine the listening port for service '{service['name']}'. "
        "Set `expose` in Compose or add Dockerfile EXPOSE."
    )


def _service_resource_name(prefix: str, service_name: str, max_length: int) -> str:
    prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-") or "ebkit"
    suffix = re.sub(r"[^a-z0-9-]+", "-", service_name.lower()).strip("-")
    suffix = suffix[:max_length - 2]
    prefix_length = max_length - len(suffix) - 1
    return f"{prefix[:prefix_length].rstrip('-') or 'e'}-{suffix}"


def _interpolate_compose_value(value: str, variables: Dict[str, str], service_name: str) -> str:
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

    def substitute(match: re.Match) -> str:
        variable, default = match.groups()
        if variable in variables:
            return variables[variable]
        if default is not None:
            return default
        raise click.ClickException(
            f"Compose service '{service_name}' references unset variable '{variable}'. "
            "Set it in .env, --env-file, or with --env KEY=VALUE."
        )

    return pattern.sub(substitute, value)


def _resolve_service_endpoints(value: str, endpoints: Dict[str, str]) -> str:
    for dependency, endpoint in endpoints.items():
        host = urlsplit(endpoint).netloc
        value = re.sub(
            r"(?P<scheme>https?://)" + re.escape(dependency) + r"(?::\d+)?(?=[:/]|$)",
            lambda match: match.group("scheme") + host,
            value,
        )
    return value


def _compose_service_environment(
    service: Dict,
    project_dir: Path,
    interpolation: Dict[str, str],
    overrides: Dict[str, str],
    endpoints: Dict[str, str],
    external_services: Tuple[str, ...],
) -> Dict[str, str]:
    config = service["config"]
    environment: Dict[str, str] = {}
    env_files = config.get("env_file", [])
    if isinstance(env_files, str):
        env_files = [env_files]
    for env_file in env_files:
        env_file_path = env_file.get("path") if isinstance(env_file, dict) else env_file
        resolved_env_file = project_dir / str(env_file_path)
        if not resolved_env_file.is_file():
            raise click.ClickException(
                f"Compose service '{service['name']}' env_file was not found: "
                f"{resolved_env_file}"
            )
        environment.update(_load_env_file(str(resolved_env_file)))

    compose_environment = config.get("environment", {})
    if isinstance(compose_environment, list):
        parsed_environment = {}
        for entry in compose_environment:
            key, separator, value = str(entry).partition("=")
            parsed_environment[key] = value if separator else None
        compose_environment = parsed_environment
    if not isinstance(compose_environment, dict):
        raise click.ClickException(
            f"Compose service '{service['name']}' has an invalid environment section."
        )

    for key, value in compose_environment.items():
        key = str(key)
        if key in overrides:
            continue
        if value is None:
            if key in interpolation:
                environment[key] = interpolation[key]
            continue
        environment[key] = _interpolate_compose_value(
            str(value), interpolation, service["name"]
        )

    environment.update(overrides)
    for key, value in environment.items():
        value = _resolve_service_endpoints(value, endpoints)
        for unavailable in set(external_services):
            if unavailable and re.search(
                r"(?P<scheme>https?://|mongodb(?:\+srv)?://|postgres(?:ql)?://|redis://)"
                + re.escape(unavailable)
                + r"(?::\d+)?(?=[:/]|$)",
                value,
                re.IGNORECASE,
            ):
                raise click.ClickException(
                    f"Service '{service['name']}' still references Compose hostname "
                    f"'{unavailable}'. Stateful services must use their managed database/cache "
                    "endpoint; web-service URLs are supplied by EBKit."
                )
        environment[key] = value
    return environment


def _compose_build_args(service: Dict, interpolation: Dict[str, str]) -> Dict[str, str]:
    build = service["build"]
    if not isinstance(build, dict):
        return {}
    args = build.get("args", {}) or {}
    if isinstance(args, list):
        args = {str(item): interpolation.get(str(item), "") for item in args}
    if not isinstance(args, dict):
        raise click.ClickException(
            f"Compose service '{service['name']}' build args must be a mapping or list."
        )
    return {
        str(key): _interpolate_compose_value(str(value), interpolation, service["name"])
        for key, value in args.items()
        if value is not None
    }


def _stage_compose_service(
    service: Dict, endpoints: Dict[str, str]
) -> tempfile.TemporaryDirectory:
    staged = tempfile.TemporaryDirectory(prefix=f"ebkit-{service['name']}-")
    staged_context = Path(staged.name) / "app"
    try:
        shutil.copytree(
            service["context"],
            staged_context,
            ignore=shutil.ignore_patterns(
                ".git", "node_modules", ".venv", "venv", "__pycache__", ".env"
            ),
        )
        config_files = (
            path
            for path in staged_context.rglob("*")
            if path.is_file()
            and path.suffix.lower() in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
            and "node_modules" not in path.parts
            and path.stat().st_size <= 1_000_000
        )
        for config_path in config_files:
            original = config_path.read_text(encoding="utf-8", errors="ignore")
            updated = original
            for dependency, endpoint in endpoints.items():
                variable = "EBKIT_SERVICE_" + re.sub(
                    r"[^A-Za-z0-9]+", "_", dependency
                ).upper() + "_URL"
                proxy_pattern = re.compile(
                    r"""target\s*:\s*(['"])https?://"""
                    + re.escape(dependency)
                    + r"""(?::\d+)?\1"""
                )
                updated = proxy_pattern.sub(
                    f"target: process.env.{variable} || '{endpoint}'", updated
                )
                updated = re.sub(
                    r"(?P<scheme>https?://)" + re.escape(dependency) + r"(?=[:/]|$)",
                    lambda match: match.group("scheme") + urlsplit(endpoint).netloc,
                    updated,
                )
            if updated != original:
                config_path.write_text(updated, encoding="utf-8")

        dockerfile_relative = service["dockerfile"].relative_to(service["context"])
        service["staged_context"] = staged_context
        service["staged_dockerfile"] = staged_context / dockerfile_relative
    except Exception:
        staged.cleanup()
        raise
    return staged


def _load_env_file(env_file: Optional[str]) -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    if not env_file or not os.path.exists(env_file):
        return env_vars
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    env_vars[k] = v
    except Exception as exc:
        _warn(f"Could not read env file '{env_file}': {exc}")
    return env_vars


def _collect_env_vars(
    proj_dir: Path,
    env_file: Optional[str],
    extra_env: Tuple[str, ...],
    is_interactive: bool,
) -> Dict[str, str]:
    merged: Dict[str, str] = {}

    dot_env = proj_dir / ".env"
    if dot_env.exists():
        _info(f"Loaded environment variables from {dot_env}")
        merged.update(_load_env_file(str(dot_env)))

    if env_file:
        merged.update(_load_env_file(env_file))

    for kv in extra_env:
        if "=" in kv:
            k, _, v = kv.partition("=")
            merged[k.strip()] = v.strip()
        else:
            _warn("Ignoring invalid --env item (expected KEY=VALUE).")

    if is_interactive and merged:
        _info(f"Loaded {len(merged)} environment variable(s).")
        placeholders = [k for k, v in merged.items() if not v or v.lower() in ("changeme", "your-key", "xxx")]
        if placeholders:
            click.echo(click.style(f"[!] {len(placeholders)} variable(s) appear unset or placeholders:", fg="yellow"))
            for k in placeholders[:5]:
                val = click.prompt(
                    f"    {k}",
                    default="",
                    show_default=False,
                    hide_input=_is_secret_environment_variable(k),
                )
                if val:
                    merged[k] = val

    for secret in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
        merged.pop(secret, None)

    return merged


def _ensure_iam_role(iam_client, role_name: str, service: str, policies: List[str]) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}],
    }
    try:
        role = iam_client.get_role(RoleName=role_name)["Role"]
        return role["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        _info(f"Creating IAM role '{role_name}' for Cluster Mode...")
        role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Created by EBReady for Elastic Beanstalk Cluster Mode",
        )["Role"]
        _ok(f"IAM role '{role_name}' created.")
    except ClientError as exc:
        _warn(f"IAM role check notice: {exc}")
        return f"arn:aws:iam:::role/{role_name}"

    for policy_arn in policies:
        try:
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except ClientError:
            pass
    return role["Arn"]


def _get_default_vpc_subnets(ec2_client) -> Tuple[Optional[str], List[str]]:
    try:
        vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            return None, []
        vpc_id = vpcs[0]["VpcId"]
        subnets = ec2_client.describe_subnets(
            Filters=[{"Name": "vpcId", "Values": [vpc_id]}, {"Name": "defaultForAz", "Values": ["true"]}]
        )["Subnets"]
        return vpc_id, [s["SubnetId"] for s in subnets]
    except Exception as exc:
        _warn(f"Notice getting default VPC subnets: {exc}")
        return None, []


def _provision_cluster_infrastructure(account_id: str, region: str) -> Dict:
    _info("Verifying Elastic Beanstalk Cluster Mode IAM roles & network...")
    iam = boto3.client("iam")
    ec2 = boto3.client("ec2", region_name=region)

    cluster_role_arn = _ensure_iam_role(
        iam, _CLUSTER_ROLES["cluster"], "eks.amazonaws.com", _CLUSTER_POLICIES["cluster"]
    )
    node_role_arn = _ensure_iam_role(
        iam, _CLUSTER_ROLES["node"], "ec2.amazonaws.com", _CLUSTER_POLICIES["node"]
    )
    obs_role_arn = _ensure_iam_role(
        iam, _CLUSTER_ROLES["observability"], "ec2.amazonaws.com", _CLUSTER_POLICIES["observability"]
    )

    try:
        iam.create_service_linked_role(AWSServiceName="elasticbeanstalk.amazonaws.com")
    except ClientError:
        pass

    op_role_arn = (
        f"arn:aws:iam::{account_id}:role/aws-service-role/"
        "elasticbeanstalk.amazonaws.com/AWSServiceRoleForElasticBeanstalk"
    )

    _, subnet_ids = _get_default_vpc_subnets(ec2)
    _ok("Cluster Mode infrastructure roles ready.")
    return {
        "cluster_role": cluster_role_arn,
        "node_role": node_role_arn,
        "observability_role": obs_role_arn,
        "operation_role": op_role_arn,
        "subnets": subnet_ids,
    }


def _ensure_ecr_repo(ecr_client, repo_name: str) -> str:
    try:
        ecr_client.create_repository(repositoryName=repo_name)
        _ok(f"ECR repository '{repo_name}' ready.")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
            _warn(f"ECR repository check: {exc}")
    repos = ecr_client.describe_repositories(repositoryNames=[repo_name])["repositories"]
    return repos[0]["repositoryUri"]


def _ecr_docker_login(ecr_client, region: str) -> bool:
    try:
        token_data = ecr_client.get_authorization_token()["authorizationData"][0]
        token = base64.b64decode(token_data["authorizationToken"]).decode("utf-8")
        user, pwd = token.split(":")
        endpoint = token_data["proxyEndpoint"]
        proc = subprocess.run(
            ["docker", "login", "-u", user, "--password-stdin", endpoint],
            input=pwd.encode("utf-8"), check=False, capture_output=True,
        )
        if proc.returncode == 0:
            _ok("Docker authenticated with ECR registry.")
            return True
        return False
    except Exception as exc:
        _warn(f"ECR login notice: {exc}")
        return False


def _ensure_eb_application(eb_client, app_name: str) -> None:
    try:
        apps = eb_client.describe_applications(ApplicationNames=[app_name]).get("Applications", [])
        if not apps:
            _info(f"Creating Elastic Beanstalk Application '{app_name}'...")
            eb_client.create_application(ApplicationName=app_name, Description="Managed by EBReady")
            _ok(f"Application '{app_name}' created.")
    except Exception as exc:
        _warn(f"EB Application check notice: {exc}")


def _build_cluster_option_settings(
    infra: Dict,
    env_vars: Dict[str, str],
    port: int = 8080,
    alb_scheme: str = "internet-facing",
    healthcheck_path: Optional[str] = None,
) -> List[Dict]:
    opts = [
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
        {"Namespace": "aws:elasticbeanstalk:eks:alb", "OptionName": "scheme", "Value": alb_scheme},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "min-replica", "Value": "1"},
        {"Namespace": "aws:elasticbeanstalk:eks:environment:autoscaling", "OptionName": "max-replica", "Value": "5"},
    ]
    if healthcheck_path:
        opts.append({
            "Namespace": "aws:elasticbeanstalk:eks:alb",
            "OptionName": "healthcheck-path",
            "Value": healthcheck_path,
        })
    if infra.get("subnets"):
        opts.append({
            "Namespace": "aws:elasticbeanstalk:eks:environment",
            "OptionName": "subnets",
            "Value": ",".join(infra["subnets"]),
        })

    all_env = dict(env_vars)
    all_env["PORT"] = str(port)
    opts.append({
        "Namespace": "aws:elasticbeanstalk:eks:environment",
        "OptionName": "env-variables",
        "Value": json.dumps(all_env),
    })

    return opts


def _ensure_cluster_environment(
    eb_client,
    app_name: str,
    env_name: str,
    version_label: str,
    infra: Dict,
    env_vars: Dict[str, str],
    port: int = 8080,
    alb_scheme: str = "internet-facing",
    healthcheck_path: Optional[str] = None,
) -> bool:
    envs = eb_client.describe_environments(
        ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
    ).get("Environments", [])
    if envs:
        status = envs[0].get("Status", "Unknown")
        tier = envs[0].get("Tier", {}).get("Name", "Unknown")
        _info(f"Target environment '{env_name}' exists (Status: {status}, Tier: {tier}).")
        return False

    _info(f"Creating new Elastic Beanstalk Cluster Mode environment '{env_name}'...")
    option_settings = _build_cluster_option_settings(
        infra, env_vars, port, alb_scheme, healthcheck_path
    )
    create_args = {
        "ApplicationName": app_name,
        "EnvironmentName": env_name,
        "VersionLabel": version_label,
        "Tier": {"Name": "Cluster", "Type": "EKS"},
        "OptionSettings": option_settings,
    }
    eb_client.create_environment(**create_args)
    _ok(f"Cluster Mode environment '{env_name}' successfully launched.")
    return True



def _register_cluster_version(
    eb_client, app_name: str, version_label: str, image_uri: str, region: str
) -> None:
    version_args = {
        "ApplicationName": app_name,
        "VersionLabel": version_label,
        "Description": f"EBReady Cluster deployment: {image_uri}",
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
                _info(f"Application version '{version_label}' already exists — reusing.")
                return
            raise RuntimeError(f"AWS CLI could not register application version: {error}")
    except ClientError as exc:
        if "already exists" in str(exc).lower():
            _info(f"Application version '{version_label}' already exists — reusing.")
            return
        raise
    _ok(f"Application version '{version_label}' registered.")


def _make_application_version_label(image_uri: str) -> str:
    version_tag = image_uri.rsplit(":", 1)[-1]
    version_suffix = str(time.time_ns())
    return f"{version_tag[:99 - len(version_suffix)]}-{version_suffix}"


def _poll_environment_health(eb_client, app_name: str, env_name: str, timeout: int = 1800) -> Dict:
    _info(f"Polling deployment status for '{env_name}'...")
    start = time.time()
    cname = None
    last_state = None
    last_health = "Unknown"
    while True:
        try:
            envs = eb_client.describe_environments(
                ApplicationName=app_name, EnvironmentNames=[env_name], IncludeDeleted=False
            ).get("Environments", [])
            if envs:
                env = envs[0]
                status = env.get("Status", "Unknown")
                health = env.get("Health", "Unknown")
                cname = env.get("CNAME")
                if (status, health) != last_state:
                    click.echo(f"    Current State: Status={status} | Health={health}")
                    last_state = (status, health)
                last_health = health
                if status == "Ready" and str(health).lower() != "grey":
                    return {
                        "status": status,
                        "health": health,
                        "url": f"http://{cname}" if cname else None,
                    }
                if status in ("Terminated", "Terminating"):
                    return {"status": status, "health": health, "url": None}
        except Exception as exc:
            click.echo(f"    Waiting... ({exc})")

        if time.time() - start > timeout:
            _warn("Reached status check timeout.")
            return {
                "status": "Timeout",
                "health": last_health,
                "url": f"http://{cname}" if cname else None,
            }
        time.sleep(15)


def _write_eb_config(cwd: Path, app: str, env: str, region: str) -> None:
    eb_dir = cwd / ".elasticbeanstalk"
    eb_dir.mkdir(exist_ok=True)
    cfg = f"""branch-defaults:
  main:
    environment: {env}
  master:
    environment: {env}
global:
  application_name: {app}
  default_platform: Docker
  default_region: {region}
  workspace_type: Application
"""
    (eb_dir / "config.yml").write_text(cfg, encoding="utf-8")


def _prompt_region(saved_region: Optional[str]) -> str:
    _section("Step 1: Select AWS Region")
    default_idx = "2"
    for i, (code, desc) in enumerate(_COMMON_REGIONS, 1):
        marker = " (current)" if code == saved_region else ""
        if code == saved_region:
            default_idx = str(i)
        click.echo(f"  {i}) {code:<15} - {desc}{marker}")
    click.echo(f"  {len(_COMMON_REGIONS)+1}) Enter custom region")
    choice = click.prompt("Select region", default=default_idx)
    try:
        idx = int(choice)
        if 1 <= idx <= len(_COMMON_REGIONS):
            return _COMMON_REGIONS[idx - 1][0]
    except ValueError:
        pass
    return click.prompt("AWS region name", default=saved_region or "us-east-2").strip()


@click.command()
@click.argument("source", default=".", required=False)
@click.option("--app", help="Elastic Beanstalk Application name")
@click.option("--environment", "--env-name", "environment_name", help="Elastic Beanstalk Environment name or multi-tier prefix")
@click.option("--region", help="AWS Region (e.g. us-east-2)")
@click.option("--tag", help="ECR image tag / URI (skips build+push if existing)")
@click.option("--repo", help="ECR repository name (default: app name)")
@click.option("--port", default=None, type=int, help="Application container port (default: auto-detect)")
@click.option("--compose", "compose_deploy", is_flag=True, help="Deploy built HTTP services from Compose to separate Cluster environments; use external databases/caches")
@click.option("--no-build", is_flag=True, default=False, help="Skip Docker build")
@click.option("--no-push", is_flag=True, default=False, help="Skip Docker push to ECR")
@click.option("--wait/--no-wait", default=True, help="Wait for environment Ready status & live URL")
@click.option("--env-file", default=None, help="Path to .env file with environment variables")
@click.option("--env", "extra_env", multiple=True, metavar="KEY=VALUE", help="Additional environment variable(s)")
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip interactive prompts (CI/CD mode)")
def deploy(source, app, environment_name, region, tag, repo, port, compose_deploy, no_build, no_push, wait, env_file, extra_env, yes):
    """
    Deploy any containerized web app to AWS Elastic Beanstalk Cluster Mode.

    SOURCE can be:
      - A local directory path (default: .)
      - A GitHub repository URL  (e.g. https://github.com/user/repo)

    Examples:
      ebkit deploy
      ebkit deploy https://github.com/user/my-repo
      ebkit deploy ./notes-app --app notes-app --environment notes-app-dev --region us-east-2 -y
      ebkit deploy ./devboard --compose --app devboard --environment devboard-prod --region us-east-2 -y
    """
    is_interactive = not yes and sys.stdin.isatty()
    temp_clone_dir: Optional[Path] = None

    if is_interactive:
        _banner()

    is_github_url = source.startswith("http://") or source.startswith("https://") or source.startswith("github.com/")
    if is_github_url:
        if not source.startswith("http"):
            source = f"https://{source}"
        ok_url, url_err = validate_github_url(source)
        if not ok_url:
            _err(f"Invalid GitHub URL: {url_err}")
            sys.exit(1)
        _info(f"Cloning safe repository: {source}...")
        try:
            temp_clone_dir = safe_clone_repo(source)
            proj_dir = temp_clone_dir
            _ok(f"Repository ready at {proj_dir}")
        except Exception as exc:
            _err(f"Safe clone failed: {exc}")
            sys.exit(1)
    else:
        proj_dir = Path(source).resolve()
        if not proj_dir.exists():
            _err(f"Source path '{proj_dir}' does not exist.")
            sys.exit(1)

    try:
        if compose_deploy:
            _execute_compose_deploy(
                proj_dir=proj_dir,
                app=app,
                environment_prefix=environment_name,
                region=region,
                repo=repo,
                env_file=env_file,
                extra_env=extra_env,
                is_interactive=is_interactive,
                tag=tag,
                port=port,
                no_build=no_build,
                no_push=no_push,
                wait=wait,
            )
        else:
            _execute_deploy(
                proj_dir=proj_dir, app=app, env=environment_name, region=region, tag=tag,
                repo=repo, port=port, no_build=no_build, no_push=no_push,
                wait=wait, env_file=env_file, extra_env=extra_env,
                is_interactive=is_interactive,
            )
    finally:
        if temp_clone_dir and temp_clone_dir.exists():
            shutil.rmtree(temp_clone_dir, ignore_errors=True)
            _info("Cleaned up temporary workspace.")


def _execute_compose_deploy(
    proj_dir: Path,
    app: Optional[str],
    environment_prefix: Optional[str],
    region: Optional[str],
    repo: Optional[str],
    env_file: Optional[str],
    extra_env: Tuple[str, ...],
    is_interactive: bool,
    tag: Optional[str],
    port: Optional[int],
    no_build: bool,
    no_push: bool,
    wait: bool,
) -> None:
    if tag or port is not None or no_build or no_push:
        raise click.ClickException(
            "--compose builds and tags each service separately; --tag, --port, --no-build, "
            "and --no-push are not supported in Compose mode."
        )
    if not wait:
        raise click.ClickException(
            "--compose requires --wait so dependent services can receive the deployed URLs."
        )
    if env_file and not Path(env_file).is_file():
        raise click.ClickException(f"Environment file not found: {env_file}")

    services, external_services = _load_compose_web_services(proj_dir)
    interpolation = _load_env_file(str(proj_dir / ".env"))
    if env_file:
        interpolation.update(_load_env_file(env_file))
    interpolation.update(os.environ)
    for item in extra_env:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise click.ClickException("Invalid --env value. Expected KEY=VALUE.")
        interpolation[key.strip()] = value.strip()
    _resolve_missing_database_variables(services, interpolation, is_interactive)
    if external_services:
        _info(
            "Compose services without Docker builds are treated as external dependencies: "
            + ", ".join(external_services)
        )
    stateful_dependencies = {
        dependency["name"]: dependency
        for service in services
        for dependency in service.get("stateful_dependencies", [])
    }
    if stateful_dependencies:
        descriptions = [
            f"{name} ({dependency['database_type'] or dependency['role']})"
            for name, dependency in stateful_dependencies.items()
        ]
        _info("Detected external database/cache services: " + ", ".join(descriptions))
    database_connections_detected = bool(stateful_dependencies)
    saved_cfg = load_config()
    if not region:
        region = (
            getattr(saved_cfg, "aws_region", None)
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-2"
        )
    if not app:
        saved_app = getattr(saved_cfg, "aws_application", None)
        app = saved_app if saved_app and saved_app != "my-app" else proj_dir.name.lower().replace("_", "-").replace(" ", "-")
    if not environment_prefix:
        environment_prefix = f"{app}-prod"
    endpoints: Dict[str, str] = {}
    deployed = []
    for service in services:
        service_name = service["name"]
        service_env = _compose_service_environment(
            service,
            proj_dir,
            interpolation,
            {},
            endpoints,
            external_services,
        )
        _validate_database_environment(service_env)
        connections = {
            (database_type, provider)
            for key, value in service_env.items()
            if _DATABASE_ENV_KEY.search(key)
            for database_type in [_database_type_from_uri(value)]
            for provider in [_database_provider(value)]
            if database_type or provider
        }
        if connections:
            database_connections_detected = True
            _info(
                f"Detected database configuration for service '{service_name}': "
                + ", ".join(
                    " via ".join(part for part in connection if part)
                    for connection in sorted(connections)
                )
            )
        elif any(
            _DATABASE_ENV_KEY.search(key) and value
            for key, value in service_env.items()
        ):
            database_connections_detected = True
        staged = _stage_compose_service(service, endpoints)
        try:
            app_name = app
            env_name = _service_resource_name(environment_prefix, service_name, 40)
            repo_name = _service_resource_name(repo or app, service_name, 256)
            is_frontend = service["role"] == "frontend"
            image_build_args = {
                key: _resolve_service_endpoints(value, endpoints)
                for key, value in _compose_build_args(service, interpolation).items()
            }
            image_build_args.update({
                key: value for key, value in service_env.items()
                if key.startswith(("VITE_", "NEXT_PUBLIC_", "REACT_APP_", "PUBLIC_"))
            })
            click.echo(
                f"Deploying Compose service '{service_name}' to "
                f"Elastic Beanstalk environment '{env_name}'..."
            )
            status = _execute_deploy(
                proj_dir=service["staged_context"],
                app=app_name,
                env=env_name,
                region=region,
                tag=None,
                repo=repo_name,
                port=_detect_service_port(service),
                no_build=False,
                no_push=False,
                wait=True,
                env_file=None,
                extra_env=tuple(f"{key}={value}" for key, value in service_env.items()),
                is_interactive=False,
                write_config=False,
                dockerfile=service["staged_dockerfile"],
                build_args=image_build_args,
                alb_scheme="internet-facing" if is_frontend else "internal",
                healthcheck_path="/" if is_frontend else "/health",
            )
        finally:
            staged.cleanup()
        if status.get("status") != "Ready" or str(status.get("health", "")).lower() not in (
            "green",
            "ok",
        ):
            raise click.ClickException(
                f"Compose service '{service_name}' did not become healthy; later services "
                "were not deployed."
            )
        endpoint = status.get("url")
        if not endpoint:
            raise click.ClickException(
                f"Compose service '{service_name}' is healthy, but Elastic Beanstalk returned "
                "no service URL."
            )
        endpoints[service_name] = endpoint
        deployed.append((service_name, endpoint))

    click.echo("")
    click.echo("Compose HTTP services reached healthy status:")
    for service_name, endpoint in deployed:
        click.echo(f"  {service_name}: {endpoint}")
    if external_services:
        click.echo(
            "External dependencies (not deployed by EBKit): "
            + ", ".join(external_services)
        )
    if database_connections_detected:
        _warn(
            "Elastic Beanstalk health confirms HTTP service health only; database connectivity "
            "is verified only if your application's health endpoint checks it."
        )
        _info(
            "EBKit does not run migrations automatically. Run the application's documented "
            "migration command against the managed database before relying on the deployment."
        )


def _compose_environment_entries(service: Dict) -> Dict[str, object]:
    entries = service["config"].get("environment", {})
    if isinstance(entries, list):
        parsed = {}
        for entry in entries:
            key, separator, value = str(entry).partition("=")
            parsed[key] = value if separator else None
        return parsed
    if not isinstance(entries, dict):
        raise click.ClickException(
            f"Compose service '{service['name']}' has an invalid environment section."
        )
    return {str(key): value for key, value in entries.items()}


def _resolve_missing_database_variables(
    services: List[Dict],
    interpolation: Dict[str, str],
    is_interactive: bool,
) -> None:
    required = []
    for service in services:
        for key, raw_value in _compose_environment_entries(service).items():
            value = "" if raw_value is None else str(raw_value)
            references = [
                (match.group(1), match.group(2))
                for match in _ENV_REFERENCE.finditer(value)
                if match.group(1)
                and match.group(1) not in interpolation
                and match.group(2) is None
            ]
            references = [
                reference
                for reference in references
                if _DATABASE_ENV_KEY.search(key)
                or _DATABASE_ENV_KEY.search(reference[0])
            ]
            if _DATABASE_ENV_KEY.search(key):
                if raw_value is None and key not in interpolation:
                    references.append((key, None))
                elif not references and not value and key not in interpolation:
                    references.append((key, None))
            for variable, _default in references:
                if variable not in required:
                    required.append(variable)

    for variable in required:
        if not is_interactive:
            raise click.ClickException(
                f"Required database variable '{variable}' is unset. Add it to a gitignored "
                ".env file, pass --env-file, or use --env KEY=VALUE."
            )
        value = click.prompt(
            f"Enter value for {variable}",
            hide_input=_is_secret_environment_variable(variable),
            show_default=False,
        )
        if not value:
            raise click.ClickException(f"Database variable '{variable}' cannot be empty.")
        interpolation[variable] = value


def _database_provider(value: str) -> Optional[str]:
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        return None
    try:
        host = urlsplit(value).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if host.endswith(".rds.amazonaws.com"):
        return "Amazon RDS"
    if host.endswith(".neon.tech"):
        return "Neon"
    if host.endswith(".supabase.co"):
        return "Supabase"
    if host.endswith(".mongodb.net"):
        return "MongoDB Atlas"
    if host.endswith(".upstash.io"):
        return "Upstash"
    if host.endswith(".redis-cloud.com") or host.endswith(".redislabs.com"):
        return "Redis Cloud"
    if host.endswith(".cache.amazonaws.com"):
        return "Amazon ElastiCache"
    return "managed external provider"


def _database_type_from_uri(value: str) -> Optional[str]:
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        return None
    scheme = urlsplit(value).scheme.lower()
    if scheme in ("postgres", "postgresql") or scheme.startswith(
        ("postgresql+", "postgres+")
    ):
        return "PostgreSQL"
    if scheme in ("mysql", "mariadb") or scheme.startswith(("mysql+", "mariadb+")):
        return "MySQL-compatible"
    if scheme in ("mongodb", "mongodb+srv"):
        return "MongoDB"
    if scheme in ("redis", "rediss"):
        return "Redis"
    return None


def _validate_database_environment(environment: Dict[str, str]) -> None:
    schemes = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "mongodb": "MongoDB",
        "mongodb+srv": "MongoDB",
        "redis": "Redis",
        "rediss": "Redis",
    }
    for key, value in environment.items():
        if not _DATABASE_ENV_KEY.search(key) or not re.match(
            r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE
        ):
            continue
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
        except ValueError:
            host = None
            parsed = urlsplit("")
        scheme = parsed.scheme.lower()
        if scheme in ("sqlite", "file"):
            raise click.ClickException(
                f"Database variable '{key}' uses a local-file database, which is not suitable "
                "for a persistent multi-service deployment. Use a managed database endpoint."
            )
        supported_driver_scheme = scheme.startswith(
            ("postgresql+", "postgres+", "mysql+", "mariadb+")
        )
        if scheme not in schemes and not supported_driver_scheme:
            raise click.ClickException(
                f"Database variable '{key}' uses an unsupported connection scheme. "
                "Supported URI schemes are PostgreSQL, MySQL/MariaDB, MongoDB, and Redis."
            )
        if not host or host.lower() in ("localhost", "127.0.0.1", "::1"):
            raise click.ClickException(
                f"Database variable '{key}' must contain a reachable managed database hostname."
            )


def _execute_deploy(
    proj_dir: Path, app, env, region, tag, repo, port,
    no_build, no_push, wait, env_file, extra_env, is_interactive, write_config=True,
    dockerfile: Optional[Path] = None, build_args: Optional[Dict[str, str]] = None,
    alb_scheme: str = "internet-facing",
    healthcheck_path: Optional[str] = None,
) -> Dict:
    saved_cfg = load_config()

    # 1. AWS Region
    if not region:
        saved_region = getattr(saved_cfg, "aws_region", None) or os.environ.get("AWS_DEFAULT_REGION")
        region = _prompt_region(saved_region) if is_interactive else (saved_region or "us-east-2")

    # 2. AWS Authentication Check
    ok, account_id, err_msg = _check_aws_credentials()
    if not ok:
        if is_interactive:
            _guide_aws_credentials(region)
            ok, account_id, _ = _check_aws_credentials()
        else:
            _err(f"AWS authentication required: {err_msg}")
            sys.exit(1)

    # 3. Architecture & Service Detection
    arch = _detect_architecture(proj_dir)
    if is_interactive:
        _section("Architecture Detected")
        click.echo(f"  Architecture: {arch['tier']}")
        for svc in arch["services"]:
            click.echo(f"  Component:    {svc['name']} ({svc['role']})")

    # 4. App & Environment Names
    saved_app = getattr(saved_cfg, "aws_application", None)
    saved_env = getattr(saved_cfg, "aws_environment", None)
    default_app = saved_app if saved_app and saved_app != "my-app" else proj_dir.name.lower().replace("_", "-").replace(" ", "-")
    if not app:
        app = click.prompt("Elastic Beanstalk Application name", default=default_app).strip() if is_interactive else default_app
    if not env:
        default_env = f"{app}-dev"
        env = click.prompt("Elastic Beanstalk Environment name", default=default_env).strip() if is_interactive else default_env

    repo_name = repo or app

    # 5. Application Port
    if not port:
        port = 8080
        dockerfile = dockerfile or proj_dir / "Dockerfile"
        if dockerfile.exists():
            try:
                for line in dockerfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip().upper().startswith("EXPOSE"):
                        p = line.split()
                        if len(p) >= 2:
                            port = int(p[1])
                            break
            except Exception:
                pass

    # 6. Environment Variables
    env_vars = _collect_env_vars(proj_dir, env_file, extra_env, is_interactive)

    # 7. Image & ECR Tag Resolution
    timestamp_tag = f"v{int(time.time())}"
    ecr_registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    ecr_tag = f"{ecr_registry}/{repo_name}:{timestamp_tag}"

    if is_interactive and not tag:
        _section("Step 3: Container Image Source")
        click.echo("  1) Build Dockerfile & push to ECR (Recommended)")
        click.echo("  2) Use existing local image and push to ECR")
        click.echo("  3) Use already-pushed ECR image tag")
        choice = click.prompt("Select option", type=click.Choice(["1", "2", "3"]), default="1")
        if choice == "1":
            tag = ecr_tag; no_build = False; no_push = False
        elif choice == "2":
            local_img = click.prompt("Local image name:tag")
            tag = ecr_tag
            subprocess.run(["docker", "tag", local_img, tag], check=False)
            no_build = True; no_push = False
        else:
            tag = click.prompt("Full ECR image URI", default=ecr_tag).strip()
            no_build = True; no_push = True
    elif not tag:
        tag = ecr_tag

    if not no_push and tag and not tag.startswith(f"{account_id}.dkr.ecr."):
        subprocess.run(["docker", "tag", tag, ecr_tag], check=False)
        tag = ecr_tag

    version_label = _make_application_version_label(tag)

    # 8. Interactive Review
    if is_interactive:
        _section("Step 4: Deployment Review")
        rows = [
            {"Field": "Source", "Value": str(proj_dir)},
            {"Field": "Architecture", "Value": arch["tier"]},
            {"Field": "Region", "Value": region},
            {"Field": "Account", "Value": account_id},
            {"Field": "Application", "Value": app},
            {"Field": "Environment", "Value": f"{env} (Cluster Mode)"},
            {"Field": "Container Image", "Value": tag},
            {"Field": "Port", "Value": str(port)},
            {"Field": "Env Variables", "Value": str(len(env_vars))},
        ]
        _table(rows, ["Field", "Value"])
        if not click.confirm("\nDeploy to AWS Elastic Beanstalk Cluster Mode?", default=True):
            click.echo("Deployment aborted.")
            sys.exit(0)

    # 9. AWS Clients
    session = boto3.Session(region_name=region)
    ecr_client = session.client("ecr")
    eb_client = session.client("elasticbeanstalk")

    # 10. Build & Push
    if not no_build:
        dockerfile = dockerfile or proj_dir / "Dockerfile"
        if not dockerfile.exists():
            _err(f"Dockerfile not found at {dockerfile}. Please run 'ebkit init' first.")
            sys.exit(1)
        _info(f"Building Docker image '{tag}'...")
        build_command = ["docker", "build", "-t", tag]
        if dockerfile:
            build_command.extend(["-f", str(dockerfile)])
        for key, value in (build_args or {}).items():
            build_command.extend(["--build-arg", f"{key}={value}"])
        build_command.append(str(proj_dir))
        res = subprocess.run(build_command, check=False)
        if res.returncode != 0:
            _err("Docker build failed.")
            sys.exit(1)
        _ok("Docker build completed.")

    if not no_push:
        _ensure_ecr_repo(ecr_client, repo_name)
        _ecr_docker_login(ecr_client, region)
        _info(f"Pushing image to ECR: {tag}...")
        push_res = subprocess.run(["docker", "push", tag], check=False)
        if push_res.returncode != 0:
            _err("Docker push failed.")
            sys.exit(1)
        _ok("Image successfully pushed to ECR.")

    # 11. Infrastructure Provisioning (Zero-config Cluster Mode)
    infra = _provision_cluster_infrastructure(account_id, region)

    # 12. Application & Version
    _ensure_eb_application(eb_client, app)
    _register_cluster_version(eb_client, app, version_label, tag, region)

    # 13. Write local config
    if write_config:
        _write_eb_config(proj_dir, app, env, region)

    # 14. Launch or Update Environment
    is_new = _ensure_cluster_environment(
        eb_client,
        app,
        env,
        version_label,
        infra,
        env_vars,
        port,
        alb_scheme,
        healthcheck_path,
    )
    if not is_new:
        _info(f"Applying version '{version_label}' to '{env}'...")
        update_kwargs = {
            "EnvironmentName": env,
            "VersionLabel": version_label,
        }
        update_kwargs["OptionSettings"] = _build_cluster_option_settings(
            infra, env_vars, port, alb_scheme, healthcheck_path
        )

        eb_client.update_environment(**update_kwargs)
        _ok("Environment update initiated.")


    # 15. Status Polling & Output Summary
    final_status = {"status": "Deploying", "health": "Pending", "url": None}
    if wait:
        final_status = _poll_environment_health(eb_client, app, env)

    click.echo("")
    click.echo(click.style("=" * 64, fg="green"))
    click.echo(click.style(f"   Deployment Summary — {app}", fg="green", bold=True))
    click.echo(click.style("=" * 64, fg="green"))

    summary_rows = []
    for svc in arch["services"]:
        summary_rows.append({
            "Service": svc["name"],
            "Role": svc["role"],
            "Status": final_status.get("status", "Launched"),
            "Health": final_status.get("health", "Unknown"),
            "URL": final_status.get("url") or "Configuring in background...",
        })
    _table(summary_rows, ["Service", "Role", "Status", "Health", "URL"])

    if final_status.get("url"):
        click.echo("")
        click.echo(click.style(f"   🚀 Live Application: {final_status['url']}", fg="bright_cyan", bold=True))
    else:
        click.echo("")
        click.echo(click.style(f"   Console: https://console.aws.amazon.com/elasticbeanstalk/home?region={region}", fg="cyan"))
    click.echo(click.style("=" * 64, fg="green"))
    return final_status


deploy_command = deploy

if __name__ == "__main__":
    deploy()
