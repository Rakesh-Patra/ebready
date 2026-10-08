"""
Diagnosis Engine Abstraction for EBReady.

Provides a unified interface for diagnosing Docker and deployment failures.
Includes GordonDiagnosisEngine (Docker AI Gordon) and FallbackDiagnosisEngine.
Never falsely claims Gordon was used if Gordon is unavailable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from ebkit.generator.docker_ai import DockerAIService

logger = logging.getLogger(__name__)


@dataclass
class DiagnosisResult:
    """Structured diagnosis returned by the diagnosis engine."""

    error: str
    root_cause: str
    severity: str  # "low", "medium", "high", "critical"
    recommended_fix: str
    safe_to_auto_fix: bool
    engine: str = "fallback"  # "gordon" or "fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DiagnosisEngine:
    """Abstract base class for diagnosis engines."""

    def diagnose(self, error: str, context: Optional[dict[str, Any]] = None) -> DiagnosisResult:
        raise NotImplementedError


class GordonDiagnosisEngine(DiagnosisEngine):
    """
    Diagnosis engine powered by installed Docker AI (Gordon).
    Strictly used only when Gordon CLI is available.
    """

    def __init__(self, docker_service: Optional[DockerAIService] = None) -> None:
        self.service = docker_service or DockerAIService()

    def diagnose(self, error: str, context: Optional[dict[str, Any]] = None) -> DiagnosisResult:
        ctx = context or {}
        working_dir = ctx.get("working_dir")
        df_content = ctx.get("dockerfile_content", "")
        cfg = ctx.get("config")

        if not self.service.is_available():
            # Fall back safely without false claiming
            fallback = FallbackDiagnosisEngine()
            return fallback.diagnose(error, context)

        prompt = f"""You are Docker AI (Gordon). Analyze this build/deployment error and provide a structured diagnosis.
Error:
{error}

Context:
{ctx}

Current Dockerfile:
{df_content}

Respond in the following format:
Root cause: <concise root cause>
Recommended fix: <concise recommended fix>
Safe to auto fix: <yes/no>
"""
        try:
            raw = self.service.ask_gordon(prompt, working_dir=working_dir)
            root_cause = "Container or configuration failure detected."
            recommended_fix = "Review Dockerfile and dependencies."
            safe_auto = False

            rc_match = re.search(r"Root cause:\s*(.+)", raw, re.IGNORECASE)
            if rc_match:
                root_cause = rc_match.group(1).strip()

            fix_match = re.search(r"Recommended fix:\s*(.+)", raw, re.IGNORECASE)
            if fix_match:
                recommended_fix = fix_match.group(1).strip()

            safe_match = re.search(r"Safe to auto fix:\s*(yes|true)", raw, re.IGNORECASE)
            if safe_match:
                safe_auto = True

            return DiagnosisResult(
                error=error,
                root_cause=root_cause,
                severity="high",
                recommended_fix=recommended_fix,
                safe_to_auto_fix=safe_auto,
                engine="gordon",
            )
        except Exception as exc:
            logger.warning("Gordon diagnosis failed, falling back: %s", exc)
            fallback = FallbackDiagnosisEngine()
            return fallback.diagnose(error, context)


class FallbackDiagnosisEngine(DiagnosisEngine):
    """
    Rule-based deterministic diagnosis engine.
    Used when Gordon is unavailable or for predictable local diagnostics.
    """

    def diagnose(self, error: str, context: Optional[dict[str, Any]] = None) -> DiagnosisResult:
        err_lower = error.lower()
        ctx = context or {}

        # 1. Missing Python dependencies
        if "modulenotfounderror" in err_lower or "no module named" in err_lower:
            m = re.search(r"no module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", err_lower)
            mod = m.group(1) if m else "dependency"
            return DiagnosisResult(
                error=error,
                root_cause=f"{mod} is missing from requirements.txt or container environment.",
                severity="high",
                recommended_fix=f"Add {mod} to requirements.txt.",
                safe_to_auto_fix=True,
                engine="fallback",
            )

        # 2. Port conflict / wrong port
        if "address already in use" in err_lower or "port is already allocated" in err_lower:
            return DiagnosisResult(
                error=error,
                root_cause="The specified application port or host port is already allocated.",
                severity="high",
                recommended_fix="Select an available port or stop the conflicting process.",
                safe_to_auto_fix=True,
                engine="fallback",
            )

        # 3. Host binding problem (127.0.0.1 vs 0.0.0.0)
        if "127.0.0.1" in error or "localhost" in error:
            return DiagnosisResult(
                error=error,
                root_cause="Application server bound to localhost (127.0.0.1) instead of 0.0.0.0.",
                severity="high",
                recommended_fix="Update host binding in CMD to 0.0.0.0.",
                safe_to_auto_fix=True,
                engine="fallback",
            )

        # 4. Docker syntax error
        if "dockerfile parse error" in err_lower or "unknown instruction" in err_lower:
            return DiagnosisResult(
                error=error,
                root_cause="Dockerfile contains invalid syntax or unknown instruction.",
                severity="high",
                recommended_fix="Correct Dockerfile syntax according to Docker specification.",
                safe_to_auto_fix=True,
                engine="fallback",
            )

        # Default fallback
        return DiagnosisResult(
            error=error,
            root_cause="Execution or build error encountered during pipeline.",
            severity="medium",
            recommended_fix="Check container logs, dependency versions, and entrypoint configuration.",
            safe_to_auto_fix=False,
            engine="fallback",
        )


def get_diagnosis_engine(prefer_gordon: bool = True) -> DiagnosisEngine:
    """Return GordonDiagnosisEngine if Gordon is available, otherwise FallbackDiagnosisEngine."""
    if prefer_gordon:
        svc = DockerAIService()
        if svc.is_available():
            return GordonDiagnosisEngine(docker_service=svc)
    return FallbackDiagnosisEngine()
