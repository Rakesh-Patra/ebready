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
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from ebkit.analyzer.scanner import ProjectScanner, ScanResult
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
            input="n\n",  # Generation/repair returns text; never authorize Gordon tool actions.
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
            if scan and scan.architecture == "MULTI_TIER":
                return self._generate_multitier_with_gemini(config, scan, working_dir)
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
- Generate the response only; do not modify project files or run build commands.
"""
        if scan and scan.architecture == "MULTI_TIER":
            prompt = (
                "Generate one root-context multi-stage Dockerfile for all application "
                "services described below. Return only a ```dockerfile fenced code "
                "block. Do not call tools, read files, modify files, or execute builds.\n"
                f"Public application listener: 0.0.0.0:{port}.\n"
                "Infer build and runtime commands from the service metadata. Run "
                "compiled applications as compiled executables, rather than a "
                "development command. Build frontend assets with their build dependencies "
                "installed; never omit dev dependencies before a frontend build. "
                "Keep databases and caches external and read connection settings "
                "from runtime environment variables. Do not embed values or placeholders. "
                "Use versioned base images compatible with each detected runtime, "
                "defaulting to public Docker Hub official images. Existing image "
                "names are hints, not requirements: do not require a specialty "
                "registry unless the user configured one for deployment. "
                "multi-stage builds, and a non-root final user. If services need separate "
                "processes, provide production-capable process management and request routing; "
                "if the backend serves frontend assets, use that existing capability. "
                f"Expose only port {port}.\n"
                + self._multitier_build_contract(scan, working_dir)
            )
        try:
            raw_output = self.ask_gordon(prompt, working_dir=working_dir)
            dockerfile = self.extract_dockerfile(raw_output)
            if not dockerfile:
                return self._gemini_multitier_fallback(
                    config,
                    scan,
                    working_dir,
                    "Docker AI (Gordon) did not return a valid Dockerfile code block.",
                )

            # Normalize and enforce required Cluster Mode safety items
            dockerfile = self._ensure_cluster_mode_safety(dockerfile, config)
            if scan and scan.architecture == "MULTI_TIER":
                validation_error = self._multitier_dockerfile_error(
                    dockerfile, config, scan, working_dir
                )
                if validation_error:
                    return self._gemini_multitier_fallback(
                        config, scan, working_dir, validation_error
                    )
            return dockerfile, "Generated successfully by Docker AI (Gordon)."
        except Exception as exc:
            logger.warning("Docker AI Dockerfile generation failed: %s", exc)
            fallback = self._gemini_multitier_fallback(
                config,
                scan,
                working_dir,
                "Docker AI (Gordon) failed to generate the Dockerfile.",
            )
            if fallback[0] is not None:
                return fallback
            if scan and scan.architecture == "MULTI_TIER":
                return fallback
            return None, str(exc)

    def _gemini_multitier_fallback(
        self,
        config: DeploymentConfig,
        scan: Optional[ScanResult],
        working_dir: Optional[Path],
        gordon_error: str,
    ) -> tuple[Optional[str], str]:
        if not scan or scan.architecture != "MULTI_TIER":
            return None, gordon_error
        dockerfile, message = self._generate_multitier_with_gemini(
            config,
            scan,
            working_dir,
        )
        if dockerfile:
            return dockerfile, message
        return None, f"{gordon_error} Gemini fallback failed: {message}"

    @staticmethod
    def _multitier_build_contract(
        scan: ScanResult, working_dir: Optional[Path]
    ) -> str:
        """Supply service metadata for any detected stack, never manifest contents."""
        if not working_dir:
            return ""
        manifest_paths = DockerAIService._multitier_project_context(working_dir).splitlines()
        directories = sorted({
            str(Path(path).parent) for path in manifest_paths
            if Path(path).name not in {"Makefile", "Procfile"}
        })
        services = []
        for relative_dir in directories:
            directory = working_dir / relative_dir
            service_scan = ProjectScanner(directory).scan()
            metadata = service_scan.as_dict()
            metadata["build_directory"] = Path(relative_dir).as_posix()
            package_file = directory / "package.json"
            if package_file.is_file():
                try:
                    package = json.loads(package_file.read_text(encoding="utf-8"))
                    if isinstance(package, dict) and isinstance(package.get("scripts", {}), dict):
                        metadata["package_script_names"] = sorted(package.get("scripts", {}))
                        metadata["has_npm_lockfile"] = (directory / "package-lock.json").is_file()
                except (OSError, ValueError, TypeError):
                    pass
            # Existing base-image choices are useful runtime evidence, not full recipes.
            dockerfile = directory / "Dockerfile"
            if dockerfile.is_file():
                content = dockerfile.read_text(encoding="utf-8", errors="replace")
                metadata["existing_base_images"] = re.findall(
                    r"(?im)^\s*FROM\s+(?:--platform=\S+\s+)?([^\s]+)", content
                )
                if re.search(r"\bnpm\s+(?:ci|install)\b[^\n]*--legacy-peer-deps\b", content):
                    metadata["npm_install_flags"] = ["--legacy-peer-deps"]
            services.append(metadata)
        context = "Detected service build metadata:\n" + json.dumps(services, indent=2)
        if DockerAIService._uses_go_frontend_contract(scan, working_dir):
            context += (
                "\nObserved serving contract: the backend serves the frontend's built "
                "dist/ assets and SPA routes using the FRONTEND_DIR environment variable. "
                "It also serves /api/ and /health. Copy the frontend build output into "
                "the final image and set FRONTEND_DIR to that exact destination in the "
                "final stage; no separate frontend server is required."
            )
        return context + "\nDo not request file-access tools; use this detected metadata.\n"

    @staticmethod
    def _multitier_project_context(working_dir: Optional[Path]) -> str:
        """Return manifest paths only; never send repository file contents to Gemini."""
        if not working_dir or not working_dir.is_dir():
            return "Project files are unavailable."

        manifest_names = {
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
            "package.json",
            "go.mod",
            "requirements.txt",
            "pyproject.toml",
            "Pipfile",
            "Gemfile",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "Cargo.toml",
            "Makefile",
            "Procfile",
            "Dockerfile",
        }
        ignored_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "vendor",
            "dist",
            "build",
        }
        selected: list[Path] = []
        for path in working_dir.rglob("*"):
            if not path.is_file() or path.name not in manifest_names:
                continue
            if any(part in ignored_dirs for part in path.relative_to(working_dir).parts):
                continue
            selected.append(path)
        selected.sort(key=lambda path: (path.name.startswith("Dockerfile"), str(path)))

        manifest_paths = [
            path.relative_to(working_dir).as_posix()
            for path in selected[:40]
        ]
        if not manifest_paths:
            return "No supported build manifests were found."
        return "\n".join(manifest_paths)

    @classmethod
    def _generate_multitier_with_gemini(
        cls,
        config: DeploymentConfig,
        scan: ScanResult,
        working_dir: Optional[Path],
    ) -> tuple[Optional[str], str]:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None, (
                "Multi-tier Dockerfile generation requires Docker AI (Gordon) or "
                "GOOGLE_API_KEY/GEMINI_API_KEY."
            )

        project_context = cls._multitier_project_context(working_dir)
        prompt = f"""Generate a production-ready SINGLE root-level Dockerfile for this
multi-tier repository for AWS Elastic Beanstalk Cluster Mode.

Detected scan (metadata only):
{json.dumps(scan.as_dict(), indent=2)}

Deployment port: {config.port}
Health check path: {config.health_check_path}
Project manifest inventory (file paths only; no file contents are sent):
{project_context}

Requirements:
- Use the repository root as the Docker build context and use multi-stage builds
  when appropriate. Preserve the service directory paths shown in the manifests.
- Build all application tiers needed for the user-facing application, then run
  them together in one container using a reliable process manager/reverse proxy
  when the architecture requires it. Do not silently omit a tier.
- Frontend builders must install build/dev dependencies; do not use --omit=dev,
  --production, or NODE_ENV=production before running the frontend build.
- Run each build in its detected build_directory where its manifest lives.
  Respect detected package-manager install flags and runtime versions.
- The final container must expose and serve the application on 0.0.0.0:{config.port}.
- Treat database, Redis, and other stateful services as external managed services.
  Do not run or bundle them in the container. Use environment variables for their
  endpoints and credentials; never invent values or copy .env files.
- Do not embed secrets, fetch scripts from arbitrary URLs, or use unpinned
  :latest base images. Run as a non-root user.
- Default to publicly pullable Docker Hub official base images for the detected
  runtimes; existing image names are evidence, not a requirement to use a registry.
- Return only one complete Dockerfile in a ```dockerfile code block. If the
  available evidence cannot produce a correct combined runtime, state that rather
  than returning a knowingly incomplete Dockerfile.
"""
        prompt += cls._multitier_build_contract(scan, working_dir)
        payload = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 8192,
                    "responseMimeType": "text/plain",
                },
            }
        ).encode("utf-8")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent?"
            + urllib.parse.urlencode({"key": api_key})
        )
        last_error = "no response"
        for attempt in range(3):
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = json.loads(response.read().decode("utf-8"))
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                dockerfile = cls.extract_dockerfile(text)
                if not dockerfile:
                    return None, "Gemini did not return a complete Dockerfile."
                dockerfile = cls._ensure_cluster_mode_safety(dockerfile, config)
                validation_error = cls._multitier_dockerfile_error(
                    dockerfile, config, scan, working_dir
                )
                if validation_error:
                    return None, validation_error
                return dockerfile, "Generated by Gemini from multi-tier project metadata."
            except urllib.error.HTTPError as exc:
                last_error = f"Gemini returned HTTP {exc.code}"
                if exc.code not in (429, 500, 503):
                    break
            except (
                urllib.error.URLError,
                TimeoutError,
                KeyError,
                IndexError,
                ValueError,
            ) as exc:
                last_error = str(exc).replace(api_key, "[REDACTED]")
            if attempt < 2:
                time.sleep(min(2**attempt, 4))
        return None, f"Gemini Dockerfile generation failed after 3 attempts: {last_error}"

    @staticmethod
    def _multitier_dockerfile_error(
        dockerfile: str,
        config: DeploymentConfig,
        scan: Optional[ScanResult] = None,
        working_dir: Optional[Path] = None,
    ) -> Optional[str]:
        """Reject an incomplete single-container recipe before writing it."""
        if not re.search(r"(?m)^\s*FROM\s+", dockerfile):
            return "Generated Dockerfile has no FROM stage."
        if not re.search(r"(?m)^\s*COPY\s+", dockerfile):
            return "Generated Dockerfile does not copy application files from the repository."
        if not re.search(r"(?m)^\s*(?:CMD|ENTRYPOINT)\s+", dockerfile):
            return "Generated Dockerfile does not define a container startup command."
        logical_lines = re.sub(r"\\\r?\n\s*", " ", dockerfile).splitlines()
        stage_lines: list[str] = []
        for line in logical_lines:
            if re.match(r"(?i)^\s*FROM\s+", line):
                stage_lines = []
            stage_lines.append(line)
            if re.search(r"(?i)^\s*RUN\s+.*\b(?:npm\s+run\s+build|pnpm\s+(?:run\s+)?build|yarn\s+(?:run\s+)?build)\b", line):
                if re.search(r"(?i)--omit(?:=|\s+)dev\b|--production\b|NODE_ENV\s*=\s*[\"']?production\b", "\n".join(stage_lines)):
                    return "Frontend builder omits development dependencies required by its build."
        exposed = {
            int(port)
            for port in re.findall(r"(?m)^\s*EXPOSE\s+(\d+)", dockerfile)
        }
        if exposed != {config.port}:
            return (
                f"Generated Dockerfile must expose only the application port "
                f"{config.port}; found {sorted(exposed)}."
            )
        if scan and working_dir and DockerAIService._uses_go_frontend_contract(
            scan, working_dir
        ):
            return DockerAIService._go_frontend_dockerfile_error(dockerfile)
        return None

    @staticmethod
    def _uses_go_frontend_contract(scan: ScanResult, working_dir: Path) -> bool:
        if scan.language != "go":
            return False
        frontend_manifest = working_dir / "frontend" / "package.json"
        go_entrypoint = working_dir / (scan.entrypoint or "")
        if not frontend_manifest.is_file() or not go_entrypoint.is_file():
            return False
        source = go_entrypoint.read_text(encoding="utf-8", errors="replace")
        return all(
            marker in source
            for marker in ("FRONTEND_DIR", "serveFrontend", "NoRoute")
        )

    @staticmethod
    def _go_frontend_dockerfile_error(dockerfile: str) -> Optional[str]:
        """Validate that Go-served frontend assets are actually built and shipped."""
        logical_lines = re.sub(r"\\\r?\n\s*", " ", dockerfile).splitlines()
        lines = logical_lines
        copy_instructions = DockerAIService._dockerfile_copy_sources(dockerfile)

        def source_path(path: str) -> str:
            return path.replace("\\", "/").removeprefix("./").rstrip("/")

        def includes_frontend(path: str) -> bool:
            normalized = source_path(path)
            if normalized in {".", "frontend"}:
                return True
            return (
                normalized.startswith("frontend/")
                and re.fullmatch(
                    r"frontend/package(?:\.json|-[^/]*|\*[^/]*)",
                    normalized,
                )
                is None
            )

        def includes_package_manifest(path: str) -> bool:
            normalized = source_path(path)
            return (
                normalized in {".", "frontend"}
                or normalized.startswith("frontend/")
                and re.fullmatch(r"frontend/package(?:\.json|\*\.json)", normalized)
                is not None
            )

        install_line = next(
            (
                index
                for index, line in enumerate(logical_lines)
                if re.search(
                    r"(?i)^\s*RUN\s+.*\b(?:npm|pnpm|yarn)\s+(?:ci|install)\b",
                    line,
                )
            ),
            len(logical_lines),
        )
        build_line = next(
            (
                index
                for index, line in enumerate(logical_lines)
                if re.search(
                    r"(?i)^\s*RUN\s+.*\b(?:npm\s+run\s+build|"
                    r"pnpm\s+(?:run\s+)?build|yarn\s+(?:run\s+)?build)\b",
                    line,
                )
            ),
            len(logical_lines),
        )

        if not any(
            index < install_line
            and any(includes_package_manifest(source) for source in sources)
            for index, sources in copy_instructions
        ):
            return "Dockerfile does not copy the frontend package manifest."
        if not any(
            index < build_line
            and any(includes_frontend(source) for source in sources)
            for index, sources in copy_instructions
        ):
            return "Dockerfile does not copy the frontend source before building it."
        if not any(
            re.search(
                r"(?i)^\s*RUN\s+.*\b(?:npm|pnpm|yarn)\s+(?:ci|install)\b",
                line,
            )
            for line in logical_lines
        ):
            return "Dockerfile does not install frontend dependencies."
        if not any(
            re.search(
                r"(?i)^\s*RUN\s+.*\b(?:npm\s+run\s+build|"
                r"pnpm\s+(?:run\s+)?build|yarn\s+(?:run\s+)?build)\b",
                line,
            )
            for line in logical_lines
        ):
            return "Dockerfile does not build the frontend assets."

        final_from = max(
            (index for index, line in enumerate(lines) if line.strip().upper().startswith("FROM ")),
            default=-1,
        )
        frontend_copy = None
        workdir = "/"
        copied_destinations = []
        for line in lines[final_from + 1 :]:
            match = re.match(r"(?i)^\s*(WORKDIR|COPY)\s+(.+)$", line)
            if not match:
                continue
            try:
                tokens = shlex.split(match.group(2))
            except ValueError:
                continue
            if match.group(1).upper() == "WORKDIR":
                if tokens:
                    workdir = posixpath.normpath(posixpath.join(workdir, tokens[0]))
                continue
            if not any(token.startswith("--from=") for token in tokens):
                continue
            paths = [token.strip("[],") for token in tokens if not token.startswith("--")]
            if len(paths) < 2:
                continue
            destination = posixpath.normpath(posixpath.join(workdir, paths[-1]))
            copied_destinations.append(destination)
            if any(posixpath.basename(posixpath.normpath(path)) == "dist" for path in paths[:-1]):
                frontend_copy = destination
        if frontend_copy is None:
            return "Dockerfile does not copy the built frontend dist/ into the final image."
        frontend_dir = None
        for line in lines[final_from + 1 :]:
            match = re.match(r"(?i)^\s*ENV\s+(.+)$", line)
            if not match:
                continue
            try:
                assignments = shlex.split(match.group(1))
            except ValueError:
                continue
            if assignments and "=" not in assignments[0]:
                if assignments[0] == "FRONTEND_DIR":
                    frontend_dir = " ".join(assignments[1:])
            else:
                for assignment in assignments:
                    key, separator, value = assignment.partition("=")
                    if separator and key == "FRONTEND_DIR":
                        frontend_dir = value
        if frontend_dir is None or posixpath.normpath(frontend_dir) != frontend_copy:
            return (
                "Dockerfile must set FRONTEND_DIR to the destination of the copied "
                "frontend dist/ assets."
            )

        if not re.search(r"(?im)^\s*RUN\s+.*\bgo\s+build\b", "\n".join(lines)):
            return "Dockerfile does not build the Go backend."
        startup = "\n".join(
            line for line in lines[final_from + 1 :]
            if re.match(r"(?i)^\s*(?:CMD|ENTRYPOINT)\s+", line)
        )
        if not startup or not any(
            path in startup or f"./{posixpath.basename(path)}" in startup
            for path in copied_destinations if path != frontend_copy
        ):
            return "Dockerfile startup command does not run the copied Go backend."
        final_stage = "\n".join(lines[final_from + 1 :])
        if not re.search(r"(?im)^\s*USER\s+(?!root\b|0\b)\S+", final_stage):
            return "Final Docker stage must run as a non-root user."
        return None

    @staticmethod
    def _dockerfile_copy_sources(dockerfile: str) -> list[tuple[int, list[str]]]:
        """Return repository source paths from COPY instructions and their line numbers."""
        logical_dockerfile = re.sub(r"\\\r?\n\s*", " ", dockerfile)
        copies = []
        for index, line in enumerate(logical_dockerfile.splitlines()):
            match = re.match(r"^\s*COPY\s+(.+?)\s*$", line, re.IGNORECASE)
            if not match:
                continue
            try:
                tokens = shlex.split(match.group(1))
            except ValueError:
                continue
            if any(token == "--from" or token.startswith("--from=") for token in tokens):
                continue
            tokens = [token for token in tokens if not token.startswith("--")]
            if len(tokens) < 2:
                continue
            sources = [token.strip("[],") for token in tokens[:-1]]
            copies.append((index, sources))
        return copies

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
