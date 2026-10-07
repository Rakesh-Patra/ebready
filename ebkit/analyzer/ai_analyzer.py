"""
AI Analyzer — abstract interface + concrete implementations.

Architecture:
  AIAnalyzer (ABC)
      ├── GoogleAIAnalyzer    (requires GOOGLE_API_KEY / GEMINI_API_KEY — Active)
      ├── OpenAIAnalyzer      (Coming Later)
      ├── ClaudeAIAnalyzer    (Coming Later)
      └── GroqAIAnalyzer      (Coming Later)

The AI is ONLY allowed to return structured JSON that validates against
DeploymentConfig.  It must NOT generate Dockerfiles, shell scripts, or
arbitrary file content.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import ValidationError

from ebkit.analyzer.scanner import ScanResult
from ebkit.models.deployment_config import (
    DeploymentConfig,
    Language,
    PackageManager,
    Platform,
    Uncertainty,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — shared by all real AI backends
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a deployment configuration analyzer.

Analyze the supplied project metadata and determine the safest deployment
configuration for AWS Elastic Beanstalk (Cluster/ECS mode).

Return ONLY a single JSON object matching the DeploymentConfig schema below.
Do NOT include any text before or after the JSON object.
Do NOT wrap the JSON in markdown code fences.
Do NOT generate Dockerfiles.
Do NOT generate shell commands beyond the start_command field.
Do NOT invent dependencies.
Prefer evidence from the scanned repository.
If information is uncertain, explicitly mark it in the "uncertainties" list
rather than inventing a value — set the field to null in that case.

DeploymentConfig JSON schema:
{
  "language":             "python | node | go | java | ruby | php | dotnet | unknown",
  "framework":            "string or null  (e.g. fastapi, express, django)",
  "runtime_version":      "string or null  (e.g. 3.12, 20)",
  "package_manager":      "pip | pipenv | poetry | npm | yarn | pnpm | maven | gradle | bundler | unknown",
  "dependency_file":      "string or null  (e.g. requirements.txt)",
  "entrypoint":           "string or null  (relative path, e.g. app/main.py)",
  "port":                 "integer 1-65535",
  "start_command":        "string  — the exact command to start the app, no shell expansion",
  "health_check_path":    "string  — HTTP path starting with /",
  "platform":             "linux/amd64 | linux/arm64",
  "architecture":         "amd64 | arm64",
  "container_strategy":   "single_stage | multi_stage | distroless",
  "eb_deployment_strategy": "docker_single | docker_multicontainer | ecs_fargate | platform_specific",
  "artifacts": {
    "dockerfile": true,
    "dockerignore": true,
    "procfile": true,
    "ebignore": true,
    "env_example": true
  },
  "environment_variables": ["KEY_NAME_ONLY", ...],
  "uncertainties":        [{"field_name": "...", "reason": "..."}]
}
"""

_USER_PROMPT_TEMPLATE = """Project scan result:

{scan_json}

Return the DeploymentConfig JSON."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class AIAnalyzer(ABC):
    """Abstract AI analyzer interface."""

    @abstractmethod
    def analyze(self, scan_result: ScanResult) -> DeploymentConfig:
        """
        Analyze scanned project metadata and return a validated DeploymentConfig.

        Raises:
            ValueError: if the AI response cannot be parsed or validated.
        """

    # Shared helper — parse and validate raw AI text
    @staticmethod
    def _parse_response(raw: str) -> DeploymentConfig:
        """
        Extract JSON from *raw* AI response and validate it against DeploymentConfig.

        Strips markdown code fences if the model included them despite instructions.
        """
        text = raw.strip()

        # Strip ```json ... ``` or ``` ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            text = "\n".join(
                line for line in lines
                if not line.startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI response is not valid JSON: {exc}\n\nRaw response:\n{raw[:500]}") from exc

        raw_artifacts = data.pop("artifacts", None) if isinstance(data, dict) else None

        try:
            config = DeploymentConfig.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"AI response failed schema validation:\n{exc}") from exc

        from ebkit.generator.artifact_planner import (
            build_default_artifact_plan,
            merge_ai_artifact_decisions,
        )
        default_plan = build_default_artifact_plan(config)
        if isinstance(raw_artifacts, dict):
            config.artifact_plan = merge_ai_artifact_decisions(default_plan, raw_artifacts)
        elif config.artifact_plan is None:
            config.artifact_plan = default_plan

        return config


# ---------------------------------------------------------------------------
# Google Gemini implementation (Primary & Active)
# ---------------------------------------------------------------------------


class GoogleAIAnalyzer(AIAnalyzer):
    """
    Production analyzer backed by Google Gemini.

    Requires GOOGLE_API_KEY or GEMINI_API_KEY in the environment.
    Supports google-generativeai SDK or native REST API client.
    """

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        self.api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable."
            )
        self.model = model
        self._genai_model = None

        try:
            import google.generativeai as genai  # type: ignore[import]
            genai.configure(api_key=self.api_key)
            self._genai_model = genai.GenerativeModel(
                model_name=model,
                system_instruction=_SYSTEM_PROMPT,
                generation_config={"temperature": 0.1, "max_output_tokens": 1024},
            )
        except Exception:
            self._genai_model = None

    def analyze(self, scan_result: ScanResult) -> DeploymentConfig:
        logger.info("[GoogleAIAnalyzer] Calling Gemini")
        user_content = _USER_PROMPT_TEMPLATE.format(
            scan_json=json.dumps(scan_result.as_dict(), indent=2)
        )

        if self._genai_model is not None:
            response = self._genai_model.generate_content(user_content)
            raw = response.text or ""
            return self._parse_response(raw)

        # Built-in REST client fallback
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_response(raw)


# ---------------------------------------------------------------------------
# Extensible Provider Stubs (Coming Later)
# ---------------------------------------------------------------------------


class OpenAIAnalyzer(AIAnalyzer):
    """Extensible provider stub for OpenAI (Coming Later)."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        raise NotImplementedError("OpenAI provider is coming later. Currently, only Gemini is available.")

    def analyze(self, scan_result: ScanResult) -> DeploymentConfig:
        raise NotImplementedError("OpenAI provider is coming later. Currently, only Gemini is available.")


class ClaudeAIAnalyzer(AIAnalyzer):
    """Extensible provider stub for Anthropic Claude (Coming Later)."""

    def __init__(self, model: str = "claude-3-5-sonnet") -> None:
        raise NotImplementedError("Claude provider is coming later. Currently, only Gemini is available.")

    def analyze(self, scan_result: ScanResult) -> DeploymentConfig:
        raise NotImplementedError("Claude provider is coming later. Currently, only Gemini is available.")


class GroqAIAnalyzer(AIAnalyzer):
    """Extensible provider stub for Groq API (Coming Later)."""

    def __init__(self, model: str = "llama-3.3-70b-versatile") -> None:
        raise NotImplementedError("Groq API provider is coming later. Currently, only Gemini is available.")

    def analyze(self, scan_result: ScanResult) -> DeploymentConfig:
        raise NotImplementedError("Groq API provider is coming later. Currently, only Gemini is available.")


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def get_analyzer(prefer: Optional[str] = None) -> AIAnalyzer:
    """
    Return the specified analyzer backend.

    Only Gemini is currently active in production.
    OpenAI, Claude, and Groq API are coming later.
    Does NOT silently fall back to another provider.
    """
    pref = (prefer or "").lower()
    if pref in ("google", "gemini", ""):
        return GoogleAIAnalyzer()
    if pref == "openai":
        raise NotImplementedError("OpenAI provider is coming later. Currently, only Gemini is available.")
    if pref == "claude":
        raise NotImplementedError("Claude provider is coming later. Currently, only Gemini is available.")
    if pref in ("groq", "groq api"):
        raise NotImplementedError("Groq API provider is coming later. Currently, only Gemini is available.")

    raise ValueError(f"Unknown AI analyzer backend: '{prefer}'")
