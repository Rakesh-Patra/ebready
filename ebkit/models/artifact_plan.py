"""
Artifact Plan Schema — strict Pydantic model for Cluster Mode.

Defines the controlled set of deployment artifacts required or optional
for AWS Elastic Beanstalk Cluster Mode.

Gemini and the scanner may only select from this controlled list.
Arbitrary file names or EC2-era configuration files (.ebextensions/*) are rejected.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


# Controlled list of supported artifacts in Cluster Mode
ALLOWED_ARTIFACTS = {
    "dockerfile": "Dockerfile",
    "dockerignore": ".dockerignore",
    "env_example": ".env.example",
    "procfile": "Procfile",
    "ebignore": ".ebignore",
}


class ArtifactRequirement(BaseModel):
    """Specification for a single deployment artifact."""

    artifact_name: str = Field(
        ...,
        description="Logical name of the artifact (dockerfile, dockerignore, env_example, procfile, ebignore).",
    )
    target_path: str = Field(
        ...,
        description="Relative destination path on disk (e.g. 'Dockerfile', '.dockerignore').",
    )
    template: str = Field(
        ...,
        description="Jinja2 template filename used to render this artifact.",
    )
    required: bool = Field(
        True,
        description="Whether this artifact is strictly required for deployment.",
    )
    optional: bool = Field(
        False,
        description="Whether this artifact is optional / supplementary.",
    )
    reason: str = Field(
        ...,
        description="Explicit explanation of why this artifact is required or optional.",
    )

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, v: str) -> str:
        if v not in ALLOWED_ARTIFACTS:
            raise ValueError(
                f"Unknown artifact name '{v}'. Allowed artifacts are: {sorted(ALLOWED_ARTIFACTS.keys())}."
            )
        return v

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, v: str) -> str:
        expected_paths = set(ALLOWED_ARTIFACTS.values())
        if v not in expected_paths:
            raise ValueError(
                f"Invalid target path '{v}'. Allowed target paths are: {sorted(expected_paths)}."
            )
        return v


class ArtifactPlan(BaseModel):
    """
    Complete artifact generation plan determined for Elastic Beanstalk Cluster Mode.

    Maps controlled artifact keys ('dockerfile', 'dockerignore', 'env_example',
    'procfile', 'ebignore') to their specifications.
    """

    artifacts: dict[str, ArtifactRequirement] = Field(
        default_factory=dict,
        description="Map of artifact logical keys to artifact requirements.",
    )

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts_keys(cls, v: dict[str, ArtifactRequirement]) -> dict[str, ArtifactRequirement]:
        for key in v:
            if key not in ALLOWED_ARTIFACTS:
                raise ValueError(
                    f"Unknown artifact key '{key}'. Allowed keys are: {sorted(ALLOWED_ARTIFACTS.keys())}."
                )
        return v

    def is_required(self, artifact_name: str) -> bool:
        """Check if an artifact is strictly required."""
        req = self.artifacts.get(artifact_name)
        return bool(req and req.required)

    def should_generate(self, artifact_name: str) -> bool:
        """Check if an artifact is planned to be generated (required or optional)."""
        req = self.artifacts.get(artifact_name)
        return bool(req and (req.required or req.optional))

    def required_targets(self) -> list[str]:
        """List target file paths for strictly required artifacts."""
        return [
            req.target_path
            for req in self.artifacts.values()
            if req.required
        ]

    def all_planned_targets(self) -> list[str]:
        """List target file paths for all planned artifacts."""
        return [
            req.target_path
            for req in self.artifacts.values()
            if req.required or req.optional
        ]

    def to_summary_dict(self) -> dict[str, dict]:
        """Return a clean dictionary representation for human and logging display."""
        return {
            name: {
                "target_path": req.target_path,
                "required": req.required,
                "optional": req.optional,
                "template": req.template,
                "reason": req.reason,
            }
            for name, req in self.artifacts.items()
        }
