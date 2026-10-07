"""
Jinja2 Deployment Kit Renderer.

Receives ONLY a validated DeploymentConfig and renders all templates.
The AI never touches this layer — it only produces a DeploymentConfig,
which is validated by Pydantic before reaching here.

Architecture:
  DeploymentConfig (validated)
         ↓
  ArtifactPlan (derived/validated)
         ↓
  DeploymentKitRenderer
         ↓
  RenderedKit (in-memory dict of filename → content)
         ↓
  Written to disk by the CLI (with safety checks)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
)

from ebkit.models.deployment_config import DeploymentConfig
from ebkit.models.artifact_plan import ArtifactPlan

logger = logging.getLogger(__name__)

# Template directory is relative to this file
_TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class RenderedKit:
    """
    In-memory collection of rendered deployment files.

    Keys are relative filenames (e.g. "Dockerfile", ".ebextensions/config.yml").
    Values are the rendered text content ready to be written to disk.
    """

    files: dict[str, str] = field(default_factory=dict)
    config: DeploymentConfig = field(default=None)  # type: ignore[assignment]
    plan: Optional[ArtifactPlan] = None
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> list[str]:
        return list(self.files.keys())


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class DeploymentKitRenderer:
    """
    Renders Jinja2 templates against a validated DeploymentConfig and ArtifactPlan.

    Templates live in ebkit/generator/templates/ and have full access
    to the config object. StrictUndefined is used so that missing
    variables raise an immediate error rather than silently rendering empty.
    """

    def __init__(self, template_dir: Path = _TEMPLATE_DIR) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,  # Deployment files are not HTML
        )
        self._template_dir = template_dir

    def render(self, config: DeploymentConfig) -> RenderedKit:
        """
        Render all planned deployment templates against *config*.

        Returns a :class:`RenderedKit` containing the rendered content
        for each file. Nothing is written to disk here.
        """
        plan = config.get_artifact_plan()
        kit = RenderedKit(config=config, plan=plan)
        ctx = {"config": config, "plan": plan}

        for name, req in plan.artifacts.items():
            if req.required or req.optional:
                try:
                    rendered_content = self._render_template(req.template, ctx)
                    kit.files[req.target_path] = rendered_content
                except Exception as exc:
                    logger.error("Failed to render artifact %s from template %s: %s", name, req.template, exc)
                    raise
            else:
                kit.skipped[name] = req.reason

        logger.debug("Rendered kit files: %s", list(kit.files.keys()))
        return kit

    def _render_template(self, template_name: str, ctx: dict) -> str:
        try:
            tmpl = self._env.get_template(template_name)
        except TemplateNotFound as exc:
            raise FileNotFoundError(
                f"Template not found: {template_name} (looked in {self._template_dir})"
            ) from exc
        return tmpl.render(**ctx)
