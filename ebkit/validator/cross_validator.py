"""
Cross-File Validator — enforces multi-artifact consistency and safety.

Checks consistency across ALL generated artifacts before anything is written
to disk or deployed:
1.  Dockerfile port == DeploymentConfig port
2.  Procfile port == DeploymentConfig port (if port specified)
3.  Application start command matches detected entrypoint
4.  Dockerfile copies required dependency files
5.  .dockerignore does not exclude required source files
6.  .ebignore does not exclude required deployment files
7.  .env.example contains variable names only (no secrets/assignments)
8.  No generated file contains secrets (API keys, AWS credentials, private keys)
9.  EB configuration matches deployment strategy & DeploymentConfig
10. YAML files are syntactically valid
11. Dockerfile syntax and instructions are valid
12. Required files exist and are non-empty
13. No unnecessary generated artifact is present

If any cross-file inconsistency occurs:
FAIL GENERATION.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

import yaml

from ebkit.generator.renderer import RenderedKit
from ebkit.models.deployment_config import DeploymentConfig


@dataclass
class ValidationReport:
    """Detailed results of cross-artifact validation."""

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


# ---------------------------------------------------------------------------
# Known Secret Patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS Secret Key Assignment", re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"][0-9a-zA-Z/+]{40}['\"]")),
    ("RSA/Private Key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub Personal Access Token", re.compile(r"\bgh[pous]_[A-Za-z0-9_]{36,}\b")),
    ("Slack API Token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b")),
    ("OpenAI API Key", re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b")),
    ("Generic High-Entropy API Key Assignment", re.compile(r"(?i)(?:api_key|secret_key|private_key|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]")),
]


def _path_matches_pattern(path_str: str, pattern: str) -> bool:
    """
    Check if a relative path (e.g. 'app/main.py') matches a .gitignore / .dockerignore pattern.
    """
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return False

    # Normalize paths to POSIX forward slashes
    clean_path = path_str.replace("\\", "/").strip("/")
    path_parts = clean_path.split("/")

    # Trailing slash means match directories
    if pattern.endswith("/"):
        dir_pat = pattern.rstrip("/")
        for part in path_parts[:-1]:
            if fnmatch.fnmatch(part, dir_pat):
                return True
        return False

    # Check exact filename or wildcard match on filename or full path
    if fnmatch.fnmatch(clean_path, pattern):
        return True

    # Check against individual parts (e.g. 'node_modules' matches 'foo/node_modules/bar.js')
    filename = path_parts[-1]
    if fnmatch.fnmatch(filename, pattern):
        return True

    for part in path_parts:
        if fnmatch.fnmatch(part, pattern):
            return True

    return False


def is_path_excluded(path_str: str, ignore_content: str) -> bool:
    """Determine if path_str is excluded by the provided ignore file content."""
    lines = [line.strip() for line in ignore_content.splitlines()]
    excluded = False
    for line in lines:
        if not line or line.startswith("#"):
            continue
        # Inversion rule
        if line.startswith("!"):
            neg_pat = line[1:].strip()
            if _path_matches_pattern(path_str, neg_pat):
                excluded = False
        else:
            if _path_matches_pattern(path_str, line):
                excluded = True
    return excluded


class CrossFileValidator:
    """
    Performs comprehensive cross-artifact consistency and security verification.
    """

    def validate(self, kit: RenderedKit) -> ValidationReport:
        report = ValidationReport()
        cfg = kit.config
        files = kit.files

        # 1. Required files presence
        self._check_required_files(kit, report)

        # 2. No unnecessary generated artifacts
        self._check_no_unnecessary_files(kit, report)

        # 3. Dockerfile validation (port, syntax, dependency copying)
        if "Dockerfile" in files:
            self._validate_dockerfile(files["Dockerfile"], cfg, report)

        # 4. Procfile validation (port, start command, entrypoint consistency)
        if "Procfile" in files:
            self._validate_procfile(files["Procfile"], cfg, report)

        # 5. .dockerignore validation (must not exclude required source files)
        if ".dockerignore" in files:
            self._validate_dockerignore(files[".dockerignore"], cfg, report)

        # 6. .ebignore validation (must not exclude required deployment files)
        if ".ebignore" in files:
            self._validate_ebignore(files[".ebignore"], cfg, files, report)

        # 7. .env.example validation (variable names only, no secrets)
        if ".env.example" in files:
            self._validate_env_example(files[".env.example"], cfg, report)

        # 8. Secret scanning across ALL generated files
        self._scan_all_files_for_secrets(files, report)

        # 9. YAML syntax and EB configuration validation
        self._validate_yaml_files(files, cfg, report)

        # 10. Start command and Entrypoint consistency
        self._validate_entrypoint_and_start_cmd(cfg, report)

        # 11. Cluster Mode configuration consistency
        self._validate_cluster_mode_consistency(cfg, report)

        return report

    def _validate_cluster_mode_consistency(
        self, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_cluster_mode_consistency")
        if cfg.cluster_environment_config is not None:
            cec = cfg.cluster_environment_config
            if cec.service_port != cfg.port:
                report.add_error(
                    f"ClusterEnvironmentConfig service_port ({cec.service_port}) "
                    f"does not match DeploymentConfig port ({cfg.port})."
                )
            if cec.readiness_probe and cec.readiness_probe.path != cfg.health_check_path:
                report.add_error(
                    f"ClusterEnvironmentConfig readiness_probe path ({cec.readiness_probe.path}) "
                    f"does not match DeploymentConfig health_check_path ({cfg.health_check_path})."
                )
            if cec.architecture and cec.architecture != cfg.architecture:
                report.add_error(
                    f"ClusterEnvironmentConfig architecture ({cec.architecture}) "
                    f"does not match DeploymentConfig architecture ({cfg.architecture})."
                )

        if cfg.cluster_build_config is not None:
            cbc = cfg.cluster_build_config
            if cbc.architecture and cbc.architecture != cfg.architecture:
                report.add_error(
                    f"ClusterBuildConfig architecture ({cbc.architecture}) "
                    f"does not match DeploymentConfig architecture ({cfg.architecture})."
                )

    def _check_required_files(self, kit: RenderedKit, report: ValidationReport) -> None:
        report.checks_run.append("check_required_files")
        plan = kit.plan or kit.config.get_artifact_plan()
        for name, req in plan.artifacts.items():
            if req.required:
                if req.target_path not in kit.files:
                    report.add_error(
                        f"Required artifact '{name}' ({req.target_path}) was not generated."
                    )
                elif not kit.files[req.target_path].strip():
                    report.add_error(
                        f"Required artifact '{name}' ({req.target_path}) is empty."
                    )

    def _check_no_unnecessary_files(self, kit: RenderedKit, report: ValidationReport) -> None:
        report.checks_run.append("check_no_unnecessary_files")
        plan = kit.plan or kit.config.get_artifact_plan()
        for name, req in plan.artifacts.items():
            if not req.required and not req.optional:
                if req.target_path in kit.files:
                    report.add_error(
                        f"Unnecessary artifact '{name}' ({req.target_path}) was generated "
                        f"despite being disabled: {req.reason}"
                    )

    def _validate_dockerfile(
        self, content: str, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_dockerfile")

        # 1. Syntax & Instructions
        valid_instructions = {
            "FROM", "WORKDIR", "COPY", "RUN", "EXPOSE", "USER", "CMD",
            "ENTRYPOINT", "ENV", "ARG", "LABEL", "HEALTHCHECK", "SHELL",
            "VOLUME", "STOPSIGNAL", "ONBUILD"
        }
        lines = [line.strip() for line in content.splitlines()]
        instructions_found = set()
        for line in lines:
            if not line or line.startswith("#"):
                continue
            first_word = line.split()[0].upper()
            if first_word in valid_instructions:
                instructions_found.add(first_word)

        if "FROM" not in instructions_found:
            report.add_error("Dockerfile syntax error: missing 'FROM' instruction.")

        # 2. Never use :latest
        for line in lines:
            if line.upper().startswith("FROM "):
                image_ref = line.split()[1] if not line.startswith("FROM --") else line.split()[2]
                if image_ref.endswith(":latest") or ":" not in image_ref.split("/")[-1]:
                    report.add_error(
                        f"Dockerfile must never use ':latest' or unversioned base image: {image_ref!r}"
                    )

        # 3. Port check
        exposed_ports = [int(m.group(1)) for m in re.finditer(r"^\s*EXPOSE\s+(\d+)", content, re.MULTILINE)]
        cmd_ports = [int(m.group(1)) for m in re.finditer(r"--port[\s=]+(\d+)", content)]

        if len(set(exposed_ports)) > 1:
            report.add_error(
                f"Conflicting port declarations in Dockerfile: multiple different ports exposed ({exposed_ports})."
            )
        if exposed_ports and cmd_ports and set(exposed_ports) != set(cmd_ports):
            report.add_error(
                f"Conflicting port declarations in Dockerfile: EXPOSE port ({exposed_ports[0]}) does not match command port ({cmd_ports[0]})."
            )

        if not exposed_ports:
            report.add_error("Dockerfile is missing 'EXPOSE' instruction.")
        else:
            exposed_port = exposed_ports[0]
            if exposed_port != cfg.port:
                report.add_error(
                    f"Dockerfile EXPOSE port ({exposed_port}) does not match DeploymentConfig port ({cfg.port})."
                )

        # 4. Dependency file copy check
        if cfg.dependency_file:
            dep_escaped = re.escape(cfg.dependency_file)
            dep_pattern = re.compile(rf"COPY\s+.*{dep_escaped}", re.MULTILINE)
            # Also accept package*.json if package.json
            pkg_json_pattern = re.compile(r"COPY\s+.*package\*\.json", re.MULTILINE)
            if not dep_pattern.search(content) and not (
                cfg.dependency_file == "package.json" and pkg_json_pattern.search(content)
            ):
                report.add_error(
                    f"Dockerfile does not copy required dependency file '{cfg.dependency_file}'."
                )

        # 5. Non-root user check for production
        if "USER " not in content:
            report.add_warning("Dockerfile does not define an unprivileged non-root USER instruction.")

    def _validate_procfile(
        self, content: str, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_procfile")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        if not lines:
            report.add_error("Procfile is empty or contains no process definitions.")
            return

        web_cmd = None
        for line in lines:
            if line.startswith("web:"):
                web_cmd = line[4:].strip()
                break

        if not web_cmd:
            report.add_error("Procfile must contain a 'web:' process command.")
            return

        # Check port consistency in Procfile command
        port_match = re.search(r"--port\s+(\d+)", web_cmd) or re.search(r":(\d{2,5})\b", web_cmd)
        if port_match:
            try:
                procfile_port = int(port_match.group(1))
                if procfile_port != cfg.port:
                    report.add_error(
                        f"Procfile port ({procfile_port}) does not match DeploymentConfig port ({cfg.port})."
                    )
            except ValueError:
                pass

    def _validate_dockerignore(
        self, content: str, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_dockerignore")

        # 1. Entrypoint must not be excluded
        if cfg.entrypoint:
            if is_path_excluded(cfg.entrypoint, content):
                report.add_error(
                    f".dockerignore excludes required application entrypoint '{cfg.entrypoint}'."
                )

        # 2. Dependency file must not be excluded
        if cfg.dependency_file:
            if is_path_excluded(cfg.dependency_file, content):
                report.add_error(
                    f".dockerignore excludes required dependency file '{cfg.dependency_file}'."
                )

        # 3. .env files should be excluded
        if not is_path_excluded(".env", content):
            report.add_warning(".dockerignore does not exclude '.env' secrets file.")

    def _validate_ebignore(
        self,
        content: str,
        cfg: DeploymentConfig,
        files: dict[str, str],
        report: ValidationReport,
    ) -> None:
        report.checks_run.append("validate_ebignore")

        # Essential EB deployment files must NEVER be excluded
        critical_files = ["Dockerfile", "Procfile"]
        if cfg.dependency_file:
            critical_files.append(cfg.dependency_file)
        if cfg.entrypoint:
            critical_files.append(cfg.entrypoint)

        for cf in critical_files:
            if cf in files or cf == cfg.dependency_file or cf == cfg.entrypoint:
                if is_path_excluded(cf, content):
                    report.add_error(
                        f".ebignore excludes critical deployment file '{cf}'."
                    )

    def _validate_env_example(
        self, content: str, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_env_example")
        lines = [line.strip() for line in content.splitlines()]

        for line in lines:
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                report.add_error(f".env.example contains invalid line without '=': {line!r}")
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                report.add_error(f".env.example contains invalid variable name: {key!r}")

            # Value rules: only empty or safe placeholders like PORT number
            if value and key != "PORT":
                # Check if it looks like an embedded secret or credential
                for label, pat in _SECRET_PATTERNS:
                    if pat.search(value):
                        report.add_error(
                            f".env.example contains a real secret value for {key} ({label})!"
                        )
                # Check for suspect token length
                if len(value) > 20 and not value.startswith(("http", "localhost")):
                    report.add_error(
                        f".env.example must only contain variable names or safe defaults, found value for {key}."
                    )

    def _scan_all_files_for_secrets(
        self, files: dict[str, str], report: ValidationReport
    ) -> None:
        report.checks_run.append("scan_all_files_for_secrets")
        for filename, content in files.items():
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(content):
                    report.add_error(
                        f"Security violation: Detected potential {label} in generated file '{filename}'."
                    )

    def _validate_yaml_files(
        self, files: dict[str, str], cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_yaml_files")
        for filename, content in files.items():
            if filename.endswith((".yml", ".yaml")):
                try:
                    parsed = yaml.safe_load(content)
                except yaml.YAMLError as exc:
                    report.add_error(f"Invalid YAML syntax in '{filename}': {exc}")
                    continue

                if not isinstance(parsed, dict):
                    report.add_error(f"YAML file '{filename}' must parse to a mapping/dictionary.")
                    continue

                # EB extension structure check
                if ".ebextensions" in filename:
                    if "option_settings" not in parsed:
                        report.add_error(
                            f"EB extension '{filename}' is missing 'option_settings' section."
                        )
                    else:
                        opt = parsed.get("option_settings", {})
                        # Check health check path consistency
                        app_opt = opt.get("aws:elasticbeanstalk:application", {})
                        if "Application Healthcheck URL" in app_opt:
                            url = app_opt["Application Healthcheck URL"]
                            if url != cfg.health_check_path:
                                report.add_error(
                                    f"EB extension healthcheck URL ({url}) does not match "
                                    f"DeploymentConfig ({cfg.health_check_path})."
                                )

    def _validate_entrypoint_and_start_cmd(
        self, cfg: DeploymentConfig, report: ValidationReport
    ) -> None:
        report.checks_run.append("validate_entrypoint_and_start_cmd")
        if not cfg.entrypoint:
            return

        ep = cfg.entrypoint.replace("\\", "/")
        ep_file = ep.split("/")[-1]
        ep_module = cfg.app_module()

        # Command should reference the entrypoint module or filename
        start_cmd = cfg.start_command
        if ep_file not in start_cmd and ep_module not in start_cmd:
            # Check if common framework pattern like main:app or app:app
            if "app" not in start_cmd and "main" not in start_cmd and "index" not in start_cmd:
                report.add_warning(
                    f"Start command '{start_cmd}' does not appear to reference entrypoint '{cfg.entrypoint}'."
                )
