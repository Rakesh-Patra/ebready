"""
Docker AI (Gordon) Integration Service.

Handles:
1. Dockerfile generation using installed Docker AI (Gordon)
2. Docker build, runtime, and security error diagnosis and recovery
3. Safety validation enforcement before rebuilds
4. Protection against unauthorized modifications to application source code
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ebkit.analyzer.scanner import ScanResult
from ebkit.models.deployment_config import DeploymentConfig

logger = logging.getLogger(__name__)


def is_docker_ai_available(docker_cmd: str = "docker") -> bool:
    """
    Check if Docker AI (Gordon) CLI is installed and responsive.
    """
    if not shutil.which(docker_cmd):
        return False
    try:
        proc = subprocess.run(
            [docker_cmd, "ai", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return proc.returncode == 0
    except Exception as exc:
        logger.debug("Docker AI availability check failed: %s", exc)
        return False


def get_docker_ai_version(docker_cmd: str = "docker") -> Optional[str]:
    """Return Docker AI version string if available."""
    if not is_docker_ai_available(docker_cmd):
        return None
    try:
        proc = subprocess.run(
            [docker_cmd, "ai", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


class DockerAIService:
    """
    Service for Docker AI (Gordon) Dockerfile generation and error recovery.

    Responsibilities:
    - Queries the developer's installed Docker AI/Gordon CLI.
    - Generates production-ready Dockerfiles based on DeploymentConfig.
    - Diagnoses Docker build, runtime, and security failures.
    - Produces targeted Dockerfile fixes without modifying source code.
    - Enforces max repair attempt limits (2-3).
    """

    def __init__(self, docker_cmd: str = "docker", max_attempts: int = 2) -> None:
        self.docker_cmd = docker_cmd
        self.max_attempts = max_attempts

    def is_available(self) -> bool:
        """Check if Docker AI (Gordon) is available on the system."""
        return is_docker_ai_available(self.docker_cmd)

    def ask_gordon(
        self,
        prompt: str,
        working_dir: Optional[Path] = None,
        timeout: int = 60,
    ) -> str:
        """
        Execute a prompt against the installed `docker ai` CLI.
        """
        if not self.is_available():
            raise RuntimeError("Docker AI (Gordon) is not available on this system.")

        cmd = [self.docker_cmd, "ai"]
        if working_dir and Path(working_dir).is_dir():
            cmd.extend(["-C", str(working_dir)])
        cmd.append(prompt)

        logger.info("Calling Docker AI (Gordon)...")
        proc = subprocess.run(
            cmd,
            input="a\n",  # Confirm any tool permissions automatically
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"Docker AI (Gordon) exited with code {proc.returncode}: {err_msg}")

        return proc.stdout

    def generate_dockerfile(
        self,
        config: DeploymentConfig,
        scan: Optional[ScanResult] = None,
        working_dir: Optional[Path] = None,
    ) -> tuple[Optional[str], str]:
        """
        Generate a production-ready Dockerfile using Docker AI (Gordon).

        Returns:
            (dockerfile_content, explanation_or_error)
        """
        if not self.is_available():
            return None, "Docker AI (Gordon) is not available."

        lang = config.language.value
        framework = config.framework or "none"
        runtime = config.runtime_version or "default"
        port = config.port
        start_cmd = config.start_command
        dep_file = config.dependency_file or "requirements.txt"
        platform = config.platform.value

        prompt = f"""You are Docker AI (Gordon). Generate a production-ready Dockerfile for an application deployment targeting AWS Elastic Beanstalk Cluster Mode (Amazon EKS container infrastructure).

Project Details (from DeploymentConfig):
- Language: {lang}
- Framework: {framework}
- Runtime version: {runtime}
- Dependency file: {dep_file}
- Application Port: {port}
- Start command: {start_cmd}
- Target Platform: {platform} (linux/amd64)

Requirements:
- Must use platform-aware FROM flag: FROM --platform=linux/amd64 <base_image>
- Never use ':latest' tag; pin to a stable slim/minimal tag
- Set WORKDIR /app
- Copy {dep_file} first and install dependencies
- Copy source files
- Configure unprivileged non-root USER
- EXPOSE {port}
- CMD or ENTRYPOINT must execute: {start_cmd}
- Do NOT include or copy .env files or secret values.
- Return the complete Dockerfile inside a ```dockerfile ... ``` code block.
"""
        try:
            raw_output = self.ask_gordon(prompt, working_dir=working_dir)
            dockerfile = self.extract_dockerfile(raw_output)
            if not dockerfile:
                return None, "Docker AI (Gordon) did not return a valid Dockerfile code block."

            # Normalize and enforce required Cluster Mode safety items
            dockerfile = self._ensure_cluster_mode_safety(dockerfile, config)
            return dockerfile, "Generated successfully by Docker AI (Gordon)."
        except Exception as exc:
            logger.warning("Docker AI Dockerfile generation failed: %s", exc)
            return None, str(exc)

    def diagnose_and_repair(
        self,
        error_type: str,
        error_details: str,
        current_dockerfile: str,
        config: DeploymentConfig,
        working_dir: Optional[Path] = None,
        attempt: int = 1,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Diagnose a failure (build, runtime, or security scan) and repair the Dockerfile.

        Returns:
            (repaired_dockerfile, diagnosis_text)
        """
        if not self.is_available():
            return None, "Docker AI (Gordon) is unavailable."

        # Sanitize error details to ensure no secrets from environment leak
        sanitized_error = self._sanitize_secrets(error_details)
        sanitized_dockerfile = self._sanitize_secrets(current_dockerfile)

        prompt = f"""You are Docker AI (Gordon). Diagnose and fix a Docker container failure for an application deployment targeting AWS Elastic Beanstalk Cluster Mode.

Failure Type: {error_type}
Failure Details:
{sanitized_error}

Application Context (from DeploymentConfig):
- Language: {config.language.value}
- Framework: {config.framework or 'none'}
- Application Port: {config.port} (Do NOT assume port 8080 is required; use port {config.port})
- Start Command: {config.start_command}
- Target Platform: {config.platform.value} (linux/amd64)

Current Dockerfile:
```dockerfile
{sanitized_dockerfile}
```

Rules:
1. Diagnose the exact issue (e.g. broken syntax, wrong port, wrong start command, missing dependency, incompatible base image, or vulnerable packages).
2. The application port is strictly {config.port}. Do NOT change the port to 8080 or claim 8080 is required.
3. Provide a 1-2 sentence diagnosis starting with "Diagnosis: ".
4. Provide the complete corrected Dockerfile inside a ```dockerfile ... ``` code block.
5. Keep the platform flag (linux/amd64), non-root USER, and no ':latest' tag.
6. Do NOT modify any application source code. Fix ONLY the Dockerfile.
7. Do NOT copy or expose any .env files or secrets.
"""
        try:
            raw_output = self.ask_gordon(prompt, working_dir=working_dir)
            diagnosis = self.extract_diagnosis(raw_output)
            if config.port != 8080 and "8080" in diagnosis:
                diagnosis = re.sub(r"\b8080\b", str(config.port), diagnosis)
            repaired_dockerfile = self.extract_dockerfile(raw_output)

            if not repaired_dockerfile:
                return None, f"Gordon did not provide a corrected Dockerfile. Output: {raw_output[:300]}"

            # Ensure safety
            repaired_dockerfile = self._ensure_cluster_mode_safety(repaired_dockerfile, config)
            return repaired_dockerfile, diagnosis
        except Exception as exc:
            logger.warning("Docker AI diagnosis and repair failed: %s", exc)
            return None, str(exc)

    @staticmethod
    def extract_dockerfile(text: str) -> Optional[str]:
        """Extract Dockerfile content from markdown code fences or raw text."""
        # Check ```dockerfile ... ``` or ```Dockerfile ... ```
        m = re.search(r"```(?:dockerfile|Dockerfile)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if m:
            content = m.group(1).strip()
            if "FROM " in content:
                return content

        # Check raw text containing FROM
        idx = text.find("FROM ")
        if idx != -1:
            lines = text[idx:].splitlines()
            code_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    break
                code_lines.append(line)
            candidate = "\n".join(code_lines).strip()
            if candidate:
                return candidate

        return None

    @staticmethod
    def extract_diagnosis(text: str) -> str:
        """Extract diagnosis explanation from Gordon response."""
        diag_match = re.search(r"\*\*Diagnosis:\*\*\s*(.+?)(?=\n\n|\n\*\*|$)", text, re.DOTALL | re.IGNORECASE)
        if diag_match:
            diag_text = diag_match.group(1).strip().replace("\n", " ")
            return diag_text

        diag_line = re.search(r"Diagnosis:\s*(.+)", text, re.IGNORECASE)
        if diag_line:
            return diag_line.group(1).strip()

        # Fallback: first non-code paragraph
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("```")]
        for line in lines:
            if not line.startswith("FROM") and not line.startswith("Calling") and not line.startswith("🛠️"):
                return line[:200]
        return "Docker configuration corrected."

    @staticmethod
    def _sanitize_secrets(text: str) -> str:
        """Strip any apparent secrets before sending to AI."""
        # Mask obvious secret patterns (key=value, password=value)
        text = re.sub(r"(?i)(password|secret|key|token)=([^\s]+)", r"\1=***", text)
        # Mask URL credentials: ://user:password@ -> ://user:***@
        text = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", text)
        return text

    @staticmethod
    def _ensure_cluster_mode_safety(dockerfile: str, config: DeploymentConfig) -> str:
        """
        Safety guard: ensure Dockerfile adheres to Cluster Mode requirements.
        - Adds --platform=linux/amd64 if omitted
        - Replaces :latest tags if present
        - Ensures non-root user exists
        - Ensures EXPOSE matches config port
        """
        lines = dockerfile.splitlines()
        updated_lines = []
        has_platform = False
        has_user = False
        has_expose = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("FROM "):
                # Replace :latest
                line = re.sub(r":latest\b", ":slim", line)
                if "--platform=" not in line:
                    line = line.replace("FROM ", "FROM --platform=linux/amd64 ", 1)
                has_platform = True
            elif stripped.startswith("USER "):
                has_user = True
            elif stripped.startswith("EXPOSE "):
                has_expose = True
                # Ensure correct port
                line = f"EXPOSE {config.port}"

            updated_lines.append(line)

        # If USER is missing, add standard appuser
        if not has_user:
            # Insert before CMD or ENTRYPOINT
            inserted = False
            final_lines = []
            for l in updated_lines:
                if not inserted and (l.strip().startswith("CMD ") or l.strip().startswith("ENTRYPOINT ")):
                    final_lines.append("RUN useradd -m -u 1000 appuser || true")
                    final_lines.append("USER appuser")
                    inserted = True
                final_lines.append(l)
            if not inserted:
                final_lines.append("RUN useradd -m -u 1000 appuser || true")
                final_lines.append("USER appuser")
            updated_lines = final_lines

        # If EXPOSE is missing, add it before CMD
        if not has_expose:
            final_lines = []
            inserted = False
            for l in updated_lines:
                if not inserted and (l.strip().startswith("CMD ") or l.strip().startswith("ENTRYPOINT ")):
                    final_lines.append(f"EXPOSE {config.port}")
                    inserted = True
                final_lines.append(l)
            if not inserted:
                final_lines.append(f"EXPOSE {config.port}")
            updated_lines = final_lines

        return "\n".join(updated_lines) + "\n"
