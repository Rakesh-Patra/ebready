"""
Deployment Configuration Schema — strict Pydantic model.

The AI analyzer must return data that validates against this schema.
No deployment file is generated from anything that fails validation.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from ebkit.models.artifact_plan import ArtifactPlan


# ---------------------------------------------------------------------------
# Enums — keep choices explicit to prevent AI hallucination
# ---------------------------------------------------------------------------


class Language(str, Enum):
    PYTHON = "python"
    NODE = "node"
    GO = "go"
    JAVA = "java"
    RUBY = "ruby"
    PHP = "php"
    DOTNET = "dotnet"
    UNKNOWN = "unknown"


class PackageManager(str, Enum):
    PIP = "pip"
    PIPENV = "pipenv"
    POETRY = "poetry"
    NPM = "npm"
    YARN = "yarn"
    PNPM = "pnpm"
    MAVEN = "maven"
    GRADLE = "gradle"
    BUNDLER = "bundler"
    UNKNOWN = "unknown"


class Platform(str, Enum):
    LINUX_AMD64 = "linux/amd64"
    LINUX_ARM64 = "linux/arm64"


class ContainerStrategy(str, Enum):
    """
    Docker container build strategy selected by the AI.

    - MULTI_STAGE: Separate build/runtime stages.  Used when there is a
      compile step (e.g. Go, Java, TypeScript).
    - SINGLE_STAGE: One stage; install deps then run.  Suitable for
      Python/Node interpreted apps.
    - DISTROLESS: Use a Google distroless runtime image.  Only appropriate
      when the app is a statically compiled binary.
    """
    MULTI_STAGE = "multi_stage"
    SINGLE_STAGE = "single_stage"
    DISTROLESS = "distroless"


class EBDeploymentStrategy(str, Enum):
    """
    AWS Elastic Beanstalk deployment mode.

    - DOCKER_SINGLE: Single-container Docker (no ECS).
    - DOCKER_MULTICONTAINER: Multi-container Docker via ECS
      (requires Dockerrun.aws.json v2).
    - ECS_FARGATE: ECS Fargate managed platform.
    - PLATFORM_SPECIFIC: Language-specific EB platform (no Docker).
    """
    DOCKER_SINGLE = "docker_single"
    DOCKER_MULTICONTAINER = "docker_multicontainer"
    ECS_FARGATE = "ecs_fargate"
    PLATFORM_SPECIFIC = "platform_specific"


# ---------------------------------------------------------------------------
# Uncertainty marker
# ---------------------------------------------------------------------------


class Uncertainty(BaseModel):
    """
    Used by the AI to flag fields where evidence is missing.

    Instead of inventing a value the AI should set a field to None and
    add an entry here explaining why.
    """

    field_name: str
    reason: str


# ---------------------------------------------------------------------------
# Cluster Mode configuration models
# ---------------------------------------------------------------------------


class ClusterBuildConfig(BaseModel):
    """
    Build configuration for Elastic Beanstalk Cluster Mode container builds.
    """

    build_type: str = Field(
        "docker",
        description="Build type (default: docker).",
    )
    dockerfile_path: str = Field(
        "Dockerfile",
        description="Path to the application Dockerfile.",
    )
    architecture: str = Field(
        "amd64",
        description="Target CPU architecture (amd64, arm64).",
    )
    timeout_minutes: Optional[int] = Field(
        20,
        ge=1,
        le=120,
        description="Build timeout in minutes.",
    )
    service_role_ref: Optional[str] = Field(
        None,
        description="Optional CodeBuild service role reference name (do not invent raw ARNs).",
    )
    build_config: Optional[dict[str, str]] = Field(
        default_factory=dict,
        description="Optional build configuration parameters.",
    )


class ProbeConfig(BaseModel):
    """
    Readiness, liveness, or startup health probe definition for Cluster Mode.
    """

    path: str = Field(
        "/health",
        description="HTTP probe path.",
    )
    port: int = Field(
        8080,
        ge=1,
        le=65535,
        description="Port for the health probe.",
    )
    initial_delay_seconds: int = Field(5, ge=0, le=300)
    period_seconds: int = Field(10, ge=1, le=120)
    timeout_seconds: int = Field(3, ge=1, le=60)
    failure_threshold: int = Field(3, ge=1, le=10)


class ClusterEnvironmentConfig(BaseModel):
    """
    Configuration for EKS-backed Elastic Beanstalk Cluster Mode environment.
    """

    service_port: int = Field(
        8080,
        ge=1,
        le=65535,
        description="Kubernetes service port.",
    )
    readiness_probe: ProbeConfig = Field(
        default_factory=ProbeConfig,
        description="Readiness probe configuration.",
    )
    liveness_probe: ProbeConfig = Field(
        default_factory=ProbeConfig,
        description="Liveness probe configuration.",
    )
    startup_probe: Optional[ProbeConfig] = Field(
        None,
        description="Optional startup probe configuration.",
    )
    min_replicas: int = Field(1, ge=1, le=50)
    max_replicas: int = Field(4, ge=1, le=100)
    cpu_limit: str = Field("500m", description="Container CPU limit (e.g. 500m).")
    memory_limit: str = Field("512Mi", description="Container memory limit (e.g. 512Mi).")
    deployment_strategy: str = Field(
        "RollingUpdate",
        description="Pod deployment strategy.",
    )
    environment_variables: list[str] = Field(
        default_factory=list,
        description="Environment variable keys passed into the pod.",
    )
    secret_references: list[str] = Field(
        default_factory=list,
        description="Secret manager references (keys only, no secret values).",
    )
    architecture: str = Field(
        "amd64",
        description="Target node architecture.",
    )


# ---------------------------------------------------------------------------
# Core deployment configuration
# ---------------------------------------------------------------------------


class DeploymentConfig(BaseModel):
    """
    Validated deployment configuration produced by the AI Analyzer.

    All generated Jinja2 templates are rendered from this model — nothing
    else.  The AI must NOT embed raw file content inside this model.
    """

    # ── Identity ─────────────────────────────────────────────────────────
    language: Language = Field(
        ...,
        description="Primary programming language of the project.",
    )
    framework: Optional[str] = Field(
        None,
        description="Web framework (e.g. fastapi, express, django).",
        max_length=64,
    )

    # ── Runtime ──────────────────────────────────────────────────────────
    runtime_version: Optional[str] = Field(
        None,
        description="Exact runtime version string (e.g. '3.12', '20').",
        max_length=16,
    )
    package_manager: PackageManager = Field(
        PackageManager.UNKNOWN,
        description="Dependency management tool.",
    )
    dependency_file: Optional[str] = Field(
        None,
        description="Primary dependency manifest filename (e.g. requirements.txt).",
        max_length=128,
    )

    # ── Application ───────────────────────────────────────────────────────
    entrypoint: Optional[str] = Field(
        None,
        description="Relative path to the application entry point.",
        max_length=256,
    )
    port: int = Field(
        8080,
        ge=1,
        le=65535,
        description="Port the application listens on.",
    )
    start_command: str = Field(
        ...,
        description="Full command used to start the application (no shell expansion).",
        min_length=3,
        max_length=512,
    )
    health_check_path: str = Field(
        "/health",
        description="HTTP path used for Elastic Beanstalk health checks.",
        max_length=256,
    )

    # ── Docker / Deployment ──────────────────────────────────────────────
    platform: Platform = Field(
        Platform.LINUX_AMD64,
        description="Docker target platform.",
    )
    architecture: str = Field(
        "amd64",
        description="CPU architecture (amd64, arm64).",
        max_length=16,
    )
    container_strategy: ContainerStrategy = Field(
        ContainerStrategy.SINGLE_STAGE,
        description=(
            "Container build strategy. "
            "Use multi_stage for compiled languages or TypeScript. "
            "Use single_stage for Python/Node interpreted apps. "
            "Use distroless ONLY for statically compiled binaries."
        ),
    )
    eb_deployment_strategy: EBDeploymentStrategy = Field(
        EBDeploymentStrategy.DOCKER_SINGLE,
        description="Elastic Beanstalk deployment mode.",
    )

    # ── Environment variables (keys only — never values) ─────────────────
    environment_variables: list[str] = Field(
        default_factory=list,
        description="List of environment variable KEYS the app needs.  No values.",
        max_length=64,
    )

    # ── Uncertainty log ───────────────────────────────────────────────────
    uncertainties: list[Uncertainty] = Field(
        default_factory=list,
        description="Fields the AI could not determine with confidence.",
    )

    # ── Cluster Mode configurations ───────────────────────────────────────
    cluster_build_config: ClusterBuildConfig = Field(
        default_factory=ClusterBuildConfig,
        description="Cluster Mode container build settings.",
    )
    cluster_environment_config: ClusterEnvironmentConfig = Field(
        default_factory=ClusterEnvironmentConfig,
        description="Cluster Mode pod and service settings.",
    )

    # ── Artifact determination plan ───────────────────────────────────────
    artifact_plan: Optional[ArtifactPlan] = Field(
        None,
        description="AI/system determination of which deployment artifacts are required or optional.",
    )

    # ── Validators ───────────────────────────────────────────────────────

    @field_validator("start_command")
    @classmethod
    def start_command_no_shell_expansion(cls, v: str) -> str:
        """Reject commands with shell expansion characters."""
        dangerous = ["$((", "`", "$(", " && ", " || ", " | ", ";"]
        for token in dangerous:
            if token in v:
                raise ValueError(
                    f"start_command must not contain shell expansion tokens ({token!r})."
                )
        return v

    @field_validator("health_check_path")
    @classmethod
    def health_check_must_start_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("health_check_path must start with '/'.")
        return v

    @field_validator("environment_variables")
    @classmethod
    def env_vars_are_keys_only(cls, v: list[str]) -> list[str]:
        """Reject any value that looks like KEY=VALUE."""
        for item in v:
            if "=" in item:
                raise ValueError(
                    f"environment_variables must contain key names only, not assignments: {item!r}"
                )
        return v

    @field_validator("framework")
    @classmethod
    def framework_lowercase(cls, v: Optional[str]) -> Optional[str]:
        return v.lower() if v else v

    @field_validator("architecture")
    @classmethod
    def architecture_supported(cls, v: str) -> str:
        if v not in ("amd64", "arm64"):
            raise ValueError(f"architecture must be 'amd64' or 'arm64', got {v!r}.")
        return v

    @field_validator("runtime_version")
    @classmethod
    def runtime_version_plausible(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Must look like a version string: digits with dots/dashes
        import re
        if not re.match(r"^\d+(\.\d+)*(-[a-z0-9]+)?$", v):
            raise ValueError(f"runtime_version {v!r} is not a valid version string.")
        return v

    @model_validator(mode="after")
    def platform_architecture_consistent(self) -> "DeploymentConfig":
        mapping = {
            Platform.LINUX_AMD64: "amd64",
            Platform.LINUX_ARM64: "arm64",
        }
        expected = mapping.get(self.platform)
        if expected and self.architecture != expected:
            # Auto-correct rather than error — the AI sometimes gets these inconsistent
            self.architecture = expected

        # Synchronize Cluster Mode settings with core fields
        self.cluster_build_config.architecture = self.architecture
        self.cluster_environment_config.architecture = self.architecture
        self.cluster_environment_config.service_port = self.port
        self.cluster_environment_config.readiness_probe.port = self.port
        self.cluster_environment_config.readiness_probe.path = self.health_check_path
        self.cluster_environment_config.liveness_probe.port = self.port
        self.cluster_environment_config.liveness_probe.path = self.health_check_path
        self.cluster_environment_config.environment_variables = list(self.environment_variables)

        return self

    @model_validator(mode="after")
    def distroless_only_for_compiled(self) -> "DeploymentConfig":
        """
        Distroless images are not suitable for Python/Node interpreted runtimes.
        Auto-correct to single_stage rather than failing — the AI may hallucinate.
        """
        if self.container_strategy == ContainerStrategy.DISTROLESS:
            if self.language in (Language.PYTHON, Language.NODE, Language.RUBY, Language.PHP):
                self.container_strategy = ContainerStrategy.SINGLE_STAGE
        return self

    # ── Convenience ──────────────────────────────────────────────────────

    def base_image(self) -> str:
        """Return a sensible Docker base image tag based on language and version."""
        ver = self.runtime_version or ""
        if self.language == Language.PYTHON:
            tag = f"python:{ver}-slim" if ver else "python:3.12-slim"
            return tag
        if self.language == Language.NODE:
            tag = f"node:{ver}-alpine" if ver else "node:20-alpine"
            return tag
        if self.language == Language.GO:
            return f"golang:{ver}-alpine" if ver else "golang:1.22-alpine"
        if self.language == Language.JAVA:
            return f"eclipse-temurin:{ver}-jre-alpine" if ver else "eclipse-temurin:21-jre-alpine"
        if self.language == Language.RUBY:
            return f"ruby:{ver}-slim" if ver else "ruby:3.3-slim"
        return "ubuntu:22.04"

    def builder_image(self) -> str:
        """Return the build-stage base image for multi-stage builds."""
        ver = self.runtime_version or ""
        if self.language == Language.GO:
            return f"golang:{ver}-alpine" if ver else "golang:1.22-alpine"
        if self.language == Language.NODE:
            # Build with full node, run with alpine
            return f"node:{ver}-alpine" if ver else "node:20-alpine"
        # Python builds and runs on the same image
        return self.base_image()

    def is_uncertain(self) -> bool:
        """True if the AI flagged any uncertainties."""
        return len(self.uncertainties) > 0

    def needs_procfile(self) -> bool:
        """True when a Procfile is required or strongly recommended."""
        # Docker Single mode on EB can use either CMD or Procfile.
        # Procfile takes precedence and is always useful for clarity.
        return self.eb_deployment_strategy in (
            EBDeploymentStrategy.DOCKER_SINGLE,
            EBDeploymentStrategy.PLATFORM_SPECIFIC,
        )

    def needs_ebextensions(self) -> bool:
        """True when .ebextensions/config.yml is required."""
        # Always useful for health check config and PORT env var
        return True

    def needs_dockerignore(self) -> bool:
        """True when a .dockerignore file is needed."""
        return self.eb_deployment_strategy != EBDeploymentStrategy.PLATFORM_SPECIFIC

    def app_module(self) -> str:
        """
        Return the Python module path for the app (e.g. 'app.main').

        Derived from entrypoint.  Falls back to 'main'.
        """
        if self.entrypoint:
            # "app/main.py" → "app.main"
            return (
                self.entrypoint
                .replace("/", ".")
                .replace("\\", ".")
                .removesuffix(".py")
            )
        return "main"

    def app_variable(self) -> str:
        """
        Return the ASGI/WSGI application variable name.

        Convention: FastAPI/Starlette/Flask use 'app'.
        """
        return "app"

    def get_artifact_plan(self) -> ArtifactPlan:
        """Return the current artifact plan, or compute a default plan if None."""
        if self.artifact_plan is not None:
            return self.artifact_plan
        from ebkit.generator.artifact_planner import build_default_artifact_plan
        return build_default_artifact_plan(self)
