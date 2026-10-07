"""
Artifact Planner — determines required and optional deployment artifacts.

Rules:
- The AI / analyzer determines which artifacts are required, optional, and why.
- The actual contents are strictly rendered by Jinja2 templates.
- Does not generate unnecessary files.
"""

from __future__ import annotations

from typing import Any, Optional
from ebkit.models.artifact_plan import ArtifactPlan, ArtifactRequirement
from ebkit.models.deployment_config import DeploymentConfig, EBDeploymentStrategy


def build_default_artifact_plan(config: DeploymentConfig) -> ArtifactPlan:
    """
    Build a standard, validated ArtifactPlan based on the DeploymentConfig.

    Determines which artifacts are required vs optional, provides explanations,
    and assigns corresponding Jinja2 template names.
    """
    artifacts: dict[str, ArtifactRequirement] = {}

    is_docker = config.eb_deployment_strategy != EBDeploymentStrategy.PLATFORM_SPECIFIC

    # 1. Dockerfile
    if is_docker:
        artifacts["dockerfile"] = ArtifactRequirement(
            artifact_name="dockerfile",
            target_path="Dockerfile",
            template="Dockerfile.j2",
            required=True,
            optional=False,
            reason=(
                f"Production container image definition for {config.language.value} "
                f"using {config.container_strategy.value} strategy on {config.platform.value}."
            ),
        )
    else:
        artifacts["dockerfile"] = ArtifactRequirement(
            artifact_name="dockerfile",
            target_path="Dockerfile",
            template="Dockerfile.j2",
            required=False,
            optional=False,
            reason="Not required: platform-specific native runtime deployment selected.",
        )

    # 2. .dockerignore
    if is_docker:
        artifacts["dockerignore"] = ArtifactRequirement(
            artifact_name="dockerignore",
            target_path=".dockerignore",
            template="dockerignore.j2",
            required=True,
            optional=False,
            reason=(
                "Prevents build-time leakage of .git, .env secrets, local virtual environments, "
                "node_modules, and build caches into the Docker image."
            ),
        )
    else:
        artifacts["dockerignore"] = ArtifactRequirement(
            artifact_name="dockerignore",
            target_path=".dockerignore",
            template="dockerignore.j2",
            required=False,
            optional=False,
            reason="Not required: Docker is not used in platform-specific deployment.",
        )

    # 3. Procfile
    # Procfile is required for platform-specific and single-container Docker for reliable supervisor start
    procfile_needed = config.eb_deployment_strategy in (
        EBDeploymentStrategy.DOCKER_SINGLE,
        EBDeploymentStrategy.PLATFORM_SPECIFIC,
    )
    artifacts["procfile"] = ArtifactRequirement(
        artifact_name="procfile",
        target_path="Procfile",
        template="Procfile.j2",
        required=procfile_needed,
        optional=not procfile_needed,
        reason=(
            f"Specifies the web process execution command ({config.start_command}) "
            "for the Elastic Beanstalk init/supervisord process."
        ),
    )

    # 4. .ebignore
    artifacts["ebignore"] = ArtifactRequirement(
        artifact_name="ebignore",
        target_path=".ebignore",
        template="ebignore.j2",
        required=True,
        optional=False,
        reason=(
            "Restricts files packaged by the EB CLI to exclude version control, "
            "test caches, local dev environments, and temporary files."
        ),
    )

    # 5. .env.example
    artifacts["env_example"] = ArtifactRequirement(
        artifact_name="env_example",
        target_path=".env.example",
        template="env.example.j2",
        required=True,
        optional=False,
        reason=(
            "Documents required application environment variable keys without embedding secrets, "
            "ensuring reproducible configuration across deployment stages."
        ),
    )

    # NOTE: .ebextensions is EC2/ASG-specific and must NOT be generated in Cluster Mode.
    # The ArtifactPlan validator enforces this — "ebextensions" is not in ALLOWED_ARTIFACTS.

    return ArtifactPlan(artifacts=artifacts)


def merge_ai_artifact_decisions(
    default_plan: ArtifactPlan,
    ai_decisions: dict[str, Any],
) -> ArtifactPlan:
    """
    Merge AI artifact determination into the default plan.

    Supports both boolean flags:
      {"dockerfile": true, "dockerignore": true, ...}
    and structured dicts:
      {"dockerfile": {"required": true, "reason": "..."}}
    """
    merged: dict[str, ArtifactRequirement] = {}

    for key, default_req in default_plan.artifacts.items():
        if key in ai_decisions:
            val = ai_decisions[key]
            if isinstance(val, bool):
                merged[key] = ArtifactRequirement(
                    artifact_name=default_req.artifact_name,
                    target_path=default_req.target_path,
                    template=default_req.template,
                    required=val,
                    optional=False if val else True,
                    reason=default_req.reason,
                )
            elif isinstance(val, dict):
                merged[key] = ArtifactRequirement(
                    artifact_name=default_req.artifact_name,
                    target_path=default_req.target_path,
                    template=val.get("template", default_req.template),
                    required=bool(val.get("required", default_req.required)),
                    optional=bool(val.get("optional", default_req.optional)),
                    reason=str(val.get("reason", default_req.reason)),
                )
            else:
                merged[key] = default_req
        else:
            merged[key] = default_req

    return ArtifactPlan(artifacts=merged)
