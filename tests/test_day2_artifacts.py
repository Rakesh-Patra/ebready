"""
Day 2 Complete Test Matrix — Artifact Generation, Validation, Docker Build & Scout.

Tests every artifact across FastAPI and Node.js (Cluster Mode):
- Dockerfile
- .dockerignore
- Procfile
- .ebignore
- .env.example

NOTE: .ebextensions is EC2/ASG-specific and is NOT generated in Cluster Mode.

Failure & Edge Case Matrix:
- Valid generation
- Invalid AI output
- Missing entrypoint
- Invalid port
- Invalid start command
- Conflicting ports
- Secrets detected
- Existing files protection
- Invalid YAML
- Docker build failure
- Docker Scout failure (security gate)
- .dockerignore accidentally excluding required files
- .ebignore accidentally excluding required files
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from ebkit.analyzer.ai_analyzer import AIAnalyzer, GoogleAIAnalyzer
from ebkit.analyzer.scanner import ProjectScanner
from ebkit.generator.artifact_planner import build_default_artifact_plan, merge_ai_artifact_decisions
from ebkit.generator.renderer import DeploymentKitRenderer, RenderedKit
from ebkit.models.deployment_config import (
    ContainerStrategy,
    DeploymentConfig,
    EBDeploymentStrategy,
    Language,
    PackageManager,
    Platform,
)
from ebkit.validator.cluster_preflight import ClusterModePreflightValidator
from ebkit.validator.cross_validator import CrossFileValidator, is_path_excluded
from ebkit.validator.docker_validator import (
    BuildResult,
    DockerBuildValidator,
    DockerRuntimeValidator,
    DockerScoutValidator,
    RuntimeResult,
    ScoutResult,
)
from ebkit.commands.init import _safe_write


def _derive_config(scan) -> DeploymentConfig:
    """Deterministic scan-to-config for testing (replaces removed MockAIAnalyzer)."""
    from typing import Optional
    lang = scan.language or "unknown"
    port = scan.detected_port or 8080
    pm = scan.package_manager or "unknown"
    dep_file: Optional[str] = scan.dependency_files[0] if scan.dependency_files else None
    start_cmd = scan.detected_start_command or f"python -m uvicorn main:app --host 0.0.0.0 --port {port}"
    runtime_version = scan.runtime_version
    if not runtime_version:
        if lang == "python":
            runtime_version = "3.12"
        elif lang == "node":
            runtime_version = "20"
    framework = scan.framework
    health_path = "/" if framework == "django" else "/health"
    env_keys: list[str] = list(scan.detected_env_vars)
    if "PORT" not in env_keys:
        env_keys.append("PORT")
    container_strategy = "multi_stage" if lang in ("go", "java") else "single_stage"
    data = {
        "language": lang, "framework": framework, "runtime_version": runtime_version,
        "package_manager": pm, "dependency_file": dep_file, "entrypoint": scan.entrypoint,
        "port": port, "start_command": start_cmd, "health_check_path": health_path,
        "platform": "linux/amd64", "architecture": "amd64",
        "container_strategy": container_strategy, "eb_deployment_strategy": "docker_single",
        "environment_variables": env_keys,
        "artifacts": {"dockerfile": True, "dockerignore": True, "procfile": True, "ebignore": True, "env_example": True},
        "uncertainties": [],
    }
    return AIAnalyzer._parse_response(json.dumps(data))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_fastapi_config() -> DeploymentConfig:
    return DeploymentConfig(
        language=Language.PYTHON,
        framework="fastapi",
        runtime_version="3.12",
        package_manager=PackageManager.PIP,
        dependency_file="requirements.txt",
        entrypoint="app/main.py",
        port=8080,
        start_command="uvicorn app.main:app --host 0.0.0.0 --port 8080",
        health_check_path="/health",
        platform=Platform.LINUX_AMD64,
        architecture="amd64",
        container_strategy=ContainerStrategy.SINGLE_STAGE,
        eb_deployment_strategy=EBDeploymentStrategy.DOCKER_SINGLE,
        environment_variables=["DATABASE_URL", "SECRET_KEY_NAME"],
    )


@pytest.fixture
def valid_node_config() -> DeploymentConfig:
    return DeploymentConfig(
        language=Language.NODE,
        framework="express",
        runtime_version="20",
        package_manager=PackageManager.NPM,
        dependency_file="package.json",
        entrypoint="index.js",
        port=3000,
        start_command="node index.js",
        health_check_path="/health",
        platform=Platform.LINUX_AMD64,
        architecture="amd64",
        container_strategy=ContainerStrategy.SINGLE_STAGE,
        eb_deployment_strategy=EBDeploymentStrategy.DOCKER_SINGLE,
        environment_variables=["NODE_ENV", "PORT"],
    )


# ===========================================================================
# 1. FastAPI Artifact Matrix
# ===========================================================================


class TestFastAPIArtifactMatrix:
    def test_fastapi_dockerfile(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        df = kit.files["Dockerfile"]

        assert "FROM --platform=linux/amd64 python:3.12-slim" in df
        assert "EXPOSE 8080" in df
        assert "COPY requirements.txt ." in df
        assert "pip install --no-cache-dir" in df
        assert "USER appuser:appgroup" in df
        assert '["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]' in df
        assert ":latest" not in df

    def test_fastapi_dockerignore(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        dignore = kit.files[".dockerignore"]

        assert ".git" in dignore
        assert ".env" in dignore
        assert "__pycache__" in dignore
        assert ".venv" in dignore
        # Ensure required source files are NOT excluded
        assert not is_path_excluded("app/main.py", dignore)
        assert not is_path_excluded("requirements.txt", dignore)

    def test_fastapi_procfile(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        procfile = kit.files["Procfile"]

        assert "web: uvicorn app.main:app --host 0.0.0.0 --port 8080" in procfile

    def test_fastapi_ebignore(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        ebignore = kit.files[".ebignore"]

        assert ".git" in ebignore
        assert ".env" in ebignore
        assert ".venv" in ebignore
        # Critical build files must not be excluded
        assert not is_path_excluded("Dockerfile", ebignore)
        assert not is_path_excluded("Procfile", ebignore)
        assert not is_path_excluded("requirements.txt", ebignore)
        assert not is_path_excluded("app/main.py", ebignore)

    def test_fastapi_env_example(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        env_ex = kit.files[".env.example"]

        assert "DATABASE_URL=" in env_ex
        assert "SECRET_KEY_NAME=" in env_ex
        assert "PORT=8080" in env_ex
        assert "password" not in env_ex.lower()

    def test_fastapi_no_ebextensions(self, valid_fastapi_config):
        """Cluster Mode must NOT generate .ebextensions — it is EC2/ASG-specific."""
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        assert ".ebextensions/config.yml" not in kit.files


# ===========================================================================
# 2. Node.js Artifact Matrix
# ===========================================================================


class TestNodeJSArtifactMatrix:
    def test_node_dockerfile(self, valid_node_config):
        kit = DeploymentKitRenderer().render(valid_node_config)
        df = kit.files["Dockerfile"]

        assert "FROM --platform=linux/amd64 node:20-alpine" in df
        assert "EXPOSE 3000" in df
        assert "COPY package*.json ./" in df
        assert "npm ci --only=production" in df
        assert "USER node" in df
        assert '["node", "index.js"]' in df

    def test_node_dockerignore(self, valid_node_config):
        kit = DeploymentKitRenderer().render(valid_node_config)
        dignore = kit.files[".dockerignore"]

        assert "node_modules" in dignore
        assert ".env" in dignore
        assert not is_path_excluded("index.js", dignore)
        assert not is_path_excluded("package.json", dignore)

    def test_node_procfile(self, valid_node_config):
        kit = DeploymentKitRenderer().render(valid_node_config)
        procfile = kit.files["Procfile"]
        assert "web: node index.js" in procfile

    def test_node_ebignore(self, valid_node_config):
        kit = DeploymentKitRenderer().render(valid_node_config)
        ebignore = kit.files[".ebignore"]

        assert "node_modules" in ebignore
        assert not is_path_excluded("Dockerfile", ebignore)
        assert not is_path_excluded("package.json", ebignore)
        assert not is_path_excluded("index.js", ebignore)

    def test_node_env_example(self, valid_node_config):
        kit = DeploymentKitRenderer().render(valid_node_config)
        env_ex = kit.files[".env.example"]

        assert "NODE_ENV=" in env_ex
        assert "PORT=3000" in env_ex

    def test_node_no_ebextensions(self, valid_node_config):
        """Cluster Mode must NOT generate .ebextensions — it is EC2/ASG-specific."""
        kit = DeploymentKitRenderer().render(valid_node_config)
        assert ".ebextensions/config.yml" not in kit.files


# ===========================================================================
# 3. Cross-File Consistency & Failure Test Matrix
# ===========================================================================


class TestCrossFileValidationMatrix:
    def test_valid_generation_passes_all_checks(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        report = CrossFileValidator().validate(kit)

        assert report.is_valid is True
        assert len(report.errors) == 0
        assert len(report.checks_run) >= 9

    def test_conflicting_ports_fails_generation(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        # Artificially modify Dockerfile EXPOSE port to conflict with config.port (8080)
        kit.files["Dockerfile"] = kit.files["Dockerfile"].replace("EXPOSE 8080", "EXPOSE 9000")

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("EXPOSE port (9000) does not match DeploymentConfig port (8080)" in err for err in report.errors)

    def test_procfile_conflicting_port_fails(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        kit.files["Procfile"] = "web: uvicorn app.main:app --port 9090"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("Procfile port (9090) does not match DeploymentConfig port (8080)" in err for err in report.errors)

    def test_invalid_port_range_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig(
                language=Language.PYTHON,
                port=70000,
                start_command="python main.py",
            )

    def test_invalid_start_command_shell_expansion_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig(
                language=Language.PYTHON,
                port=8080,
                start_command="uvicorn app:app --port 8080; rm -rf /",
            )

    def test_secret_detection_fails_generation(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        # Inject AWS Access Key into a generated file
        kit.files[".env.example"] = "PORT=8080\nAWS_KEY=AKIAIOSFODNN7EXAMPLE\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("Security violation" in err or "secret value" in err for err in report.errors)

    def test_rsa_private_key_fails_generation(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        kit.files["Dockerfile"] += "\n# -----BEGIN RSA PRIVATE KEY-----\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("RSA/Private Key" in err for err in report.errors)

    def test_dockerignore_accidentally_excluding_entrypoint_fails(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        # Accidentally exclude entrypoint in .dockerignore
        kit.files[".dockerignore"] += "\napp/main.py\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any(".dockerignore excludes required application entrypoint" in err for err in report.errors)

    def test_dockerignore_accidentally_excluding_dependency_file_fails(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        kit.files[".dockerignore"] += "\nrequirements.txt\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any(".dockerignore excludes required dependency file" in err for err in report.errors)

    def test_ebignore_accidentally_excluding_dockerfile_fails(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        kit.files[".ebignore"] += "\nDockerfile\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any(".ebignore excludes critical deployment file 'Dockerfile'" in err for err in report.errors)

    def test_no_ebextensions_generated_in_cluster_mode(self, valid_fastapi_config):
        """Verify cross-validator does not flag missing .ebextensions — it should never be in kit.files."""
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        assert ".ebextensions/config.yml" not in kit.files
        report = CrossFileValidator().validate(kit)
        # The absence of .ebextensions/config.yml must not cause a validation error
        ebext_errors = [e for e in report.errors if "ebextensions" in e.lower()]
        assert len(ebext_errors) == 0, f"Unexpected ebextensions errors: {ebext_errors}"

    def test_missing_required_file_fails(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        del kit.files["Dockerfile"]

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("Required artifact 'dockerfile'" in err for err in report.errors)

    def test_unnecessary_disabled_artifact_present_fails(self, valid_fastapi_config):
        # Configure platform-specific strategy where Dockerfile is disabled
        cfg = valid_fastapi_config.model_copy()
        cfg.eb_deployment_strategy = EBDeploymentStrategy.PLATFORM_SPECIFIC
        plan = cfg.get_artifact_plan()
        cfg.artifact_plan = plan

        kit = RenderedKit(config=cfg, plan=plan)
        # Illegally include Dockerfile when it was disabled
        kit.files["Dockerfile"] = "FROM python:3.12\n"

        report = CrossFileValidator().validate(kit)
        assert report.is_valid is False
        assert any("Unnecessary artifact 'dockerfile'" in err for err in report.errors)


# ===========================================================================
# 4. Artifact Planning & Rationale Matrix
# ===========================================================================


class TestArtifactPlanning:
    def test_ai_artifact_determination_and_rationale(self, valid_fastapi_config):
        plan = build_default_artifact_plan(valid_fastapi_config)

        assert plan.is_required("dockerfile") is True
        assert plan.is_required("dockerignore") is True
        assert plan.is_required("procfile") is True
        assert plan.is_required("ebignore") is True
        assert plan.is_required("env_example") is True
        # ebextensions must NOT be present in Cluster Mode plan
        assert "ebextensions" not in plan.artifacts

        summary = plan.to_summary_dict()
        for name, spec in summary.items():
            assert "reason" in spec
            assert len(spec["reason"]) > 10
            assert "template" in spec

    def test_ai_boolean_decision_merging(self, valid_fastapi_config):
        default_plan = build_default_artifact_plan(valid_fastapi_config)
        ai_decisions = {
            "dockerfile": True,
            "dockerignore": True,
            "procfile": False,
            "ebignore": True,
            "env_example": True,
            # ebextensions intentionally omitted — not allowed in Cluster Mode
        }
        merged = merge_ai_artifact_decisions(default_plan, ai_decisions)

        assert merged.is_required("dockerfile") is True
        assert merged.is_required("procfile") is False
        assert "ebextensions" not in merged.artifacts

    def test_multi_stage_container_strategy(self):
        cfg = DeploymentConfig(
            language=Language.GO,
            runtime_version="1.22",
            port=8080,
            start_command="./server",
            container_strategy=ContainerStrategy.MULTI_STAGE,
        )
        kit = DeploymentKitRenderer().render(cfg)
        df = kit.files["Dockerfile"]

        assert "AS builder" in df
        assert "AS runner" in df
        assert "golang:1.22-alpine" in df


# ===========================================================================
# 5. Docker Build & Scout Security Gate
# ===========================================================================


class TestDockerBuildAndScoutValidators:
    def test_docker_build_failure_captured(self, tmp_path: Path):
        validator = DockerBuildValidator()
        with patch.object(validator, "is_docker_available", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="ERROR: failed to solve: invalid syntax",
                )
                res = validator.build(tmp_path, image_tag="test:fail")
                assert res.success is False
                assert "invalid syntax" in res.error

    def test_docker_scout_gate_success(self):
        validator = DockerScoutValidator(max_critical=0, max_high=5)
        with patch.object(validator, "is_scout_available", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "vulnerabilities": [
                            {"severity": "MEDIUM", "id": "CVE-2024-001"},
                            {"severity": "LOW", "id": "CVE-2024-002"},
                        ]
                    }),
                    stderr="",
                )
                res = validator.scan("test:clean")
                assert res.gate_passed is True
                assert res.critical_count == 0
                assert res.high_count == 0
                assert res.medium_count == 1
                assert "Security gate passed" in res.gate_reason

    def test_docker_scout_gate_failure_on_critical_cve(self):
        validator = DockerScoutValidator(max_critical=0, max_high=5)
        with patch.object(validator, "is_scout_available", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "vulnerabilities": [
                            {"severity": "CRITICAL", "id": "CVE-2024-9999"},
                            {"severity": "HIGH", "id": "CVE-2024-8888"},
                        ]
                    }),
                    stderr="",
                )
                res = validator.scan("test:vulnerable")
                assert res.gate_passed is False
                assert res.critical_count == 1
                assert "Critical CVEs (1) exceed security gate threshold (0)" in res.gate_reason

    def test_distinguishes_build_success_from_security_validation(self):
        build_res = BuildResult(success=True, image_tag="my-app:prod", build_time_seconds=4.2)
        scout_res = ScoutResult(gate_passed=False, critical_count=2, gate_reason="2 Critical CVEs")

        assert build_res.success is True
        assert scout_res.gate_passed is False


# ===========================================================================
# 6. Existing File Overwrite Protection
# ===========================================================================


class TestExistingFilesSafety:
    def test_safe_write_protects_existing_file_without_force(self, tmp_path: Path):
        target = tmp_path / "Dockerfile"
        target.write_text("ORIGINAL CONTENT")

        # User declines overwrite
        with patch("click.confirm", return_value=False):
            ok, status = _safe_write(tmp_path, "Dockerfile", "NEW CONTENT", force=False)
            assert ok is False
            assert "skipped" in status
            assert target.read_text() == "ORIGINAL CONTENT"

    def test_safe_write_overwrites_with_force(self, tmp_path: Path):
        target = tmp_path / "Dockerfile"
        target.write_text("ORIGINAL CONTENT")

        ok, status = _safe_write(tmp_path, "Dockerfile", "NEW CONTENT", force=True)
        assert ok is True
        assert "overwritten (--force)" in status
        assert target.read_text() == "NEW CONTENT"


# ===========================================================================
# 7. DockerRuntimeValidator
# ===========================================================================


class TestDockerRuntimeValidator:
    def test_runtime_result_passed_property(self):
        res = RuntimeResult(root_ok=True, health_ok=True)
        assert res.passed is True

    def test_runtime_result_fails_if_either_probe_fails(self):
        assert RuntimeResult(root_ok=True, health_ok=False).passed is False
        assert RuntimeResult(root_ok=False, health_ok=True).passed is False
        assert RuntimeResult(root_ok=False, health_ok=False).passed is False

    def test_runtime_validator_unavailable_docker(self, tmp_path: Path):
        """When Docker is not in PATH the validator returns error, does not raise."""
        validator = DockerRuntimeValidator(docker_cmd="__nonexistent_docker__")
        result = validator.validate("some-image:tag", port=8080)
        assert result.passed is False
        assert result.error is not None
        assert "not found" in result.error.lower() or "unavailable" in result.error.lower() or result.error

    def test_runtime_validator_container_start_failure(self, tmp_path: Path):
        """If docker run fails, validator returns error without raising."""
        validator = DockerRuntimeValidator()
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="No such image: nonexistent:tag",
                )
                result = validator.validate("nonexistent:tag", port=8080)
        assert result.passed is False
        assert result.error or (not result.root_ok and not result.health_ok)

    def test_runtime_validator_successful_probe(self):
        """Simulate a successful probe by mocking docker calls and health checker."""
        from ebkit.validator.health_checker import HealthCheckResult

        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="container-id\n", stderr="")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55020/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:prod", port=8080)

        assert result.root_ok is True
        assert result.health_ok is True
        assert result.passed is True
        assert result.is_running is True


# ===========================================================================
# 8. Secret Safety Test
# ===========================================================================


class TestSecretSafety:
    """Verify that real secret values never appear in any generated artifact."""

    def test_secret_value_not_in_any_artifact(self, tmp_path: Path):
        """
        Write a .env file with a real secret into a temp project.
        After full pipeline, the secret value must NOT appear in any artifact.
        """
        (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            'import os\nfrom fastapi import FastAPI\napp = FastAPI()\n'
            '@app.get("/")\ndef root(): return {}\n'
            '@app.get("/health")\ndef health(): return {"status": "ok"}\n'
        )
        env_content = (
            "TEST_SECRET=super-secret-value\n"
            "DATABASE_URL=postgres://user:pass@localhost/db\n"
            "PORT=8080\n"
        )
        (tmp_path / ".env").write_text(env_content)

        scan = ProjectScanner(tmp_path).scan()
        config = _derive_config(scan)
        kit = DeploymentKitRenderer().render(config)

        for filename, content in kit.files.items():
            assert "super-secret-value" not in content, (
                f"Secret value leaked into {filename}"
            )
            assert "postgres://user:pass" not in content, (
                f"DB credential leaked into {filename}"
            )

    def test_env_example_has_key_without_value(self, tmp_path: Path):
        """
        .env.example must contain TEST_SECRET= (key) but NOT =super-secret-value.
        """
        (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            'import os\nfrom fastapi import FastAPI\napp = FastAPI()\n'
        )
        (tmp_path / ".env").write_text("TEST_SECRET=super-secret-value\nPORT=8080\n")

        scan = ProjectScanner(tmp_path).scan()
        config = _derive_config(scan)
        if "TEST_SECRET" not in config.environment_variables:
            config = config.model_copy(update={"environment_variables": config.environment_variables + ["TEST_SECRET"]})
        kit = DeploymentKitRenderer().render(config)

        env_example = kit.files[".env.example"]
        assert "super-secret-value" not in env_example
        assert "TEST_SECRET=" in env_example


# ===========================================================================
# 9. CLI Dry-Run Test
# ===========================================================================


class TestCLIDryRun:
    """Test that --dry-run displays the plan but writes no files."""

    def test_dry_run_writes_no_files(self, tmp_path: Path, monkeypatch):
        from click.testing import CliRunner
        from ebkit.commands.init import init_command

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
        )

        scan = ProjectScanner(tmp_path).scan()
        dummy_config = _derive_config(scan)

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--path", str(tmp_path), "--analyzer", "gemini", "--dry-run", "--yes"],
                )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        assert "[DRY RUN] No files were written." in result.output

        assert not (tmp_path / "Dockerfile").exists()
        assert not (tmp_path / ".dockerignore").exists()
        assert not (tmp_path / "Procfile").exists()
        assert not (tmp_path / ".env.example").exists()

    def test_dry_run_shows_deployment_plan(self, tmp_path: Path, monkeypatch):
        from click.testing import CliRunner
        from ebkit.commands.init import init_command

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
        )

        scan = ProjectScanner(tmp_path).scan()
        dummy_config = _derive_config(scan)

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--path", str(tmp_path), "--analyzer", "gemini", "--dry-run", "--yes"],
                )

        assert "EBReady Deployment Plan" in result.output
        assert "Artifacts:" in result.output
        assert "Dockerfile" in result.output


# ===========================================================================
# 10. Cluster Mode Preflight Validator
# ===========================================================================


class TestClusterModePreflight:
    """Verifies the Cluster Mode Preflight evaluation."""

    def test_valid_kit_passes_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        report = ClusterModePreflightValidator().evaluate(kit=kit)

        assert report.ready_for_deployment is True
        assert len(report.failures) == 0
        check_names = [c.name for c in report.checks]
        assert "Architecture" in check_names
        assert "Service Port" in check_names
        assert "Health Endpoint" in check_names
        assert "Artifact Purity" in check_names
        assert "Immutable Base Image" in check_names

    def test_legacy_ebextensions_fails_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        # Inject legacy EC2 .ebextensions file
        kit.files[".ebextensions/config.yml"] = "option_settings: {}"

        report = ClusterModePreflightValidator().evaluate(kit=kit)
        assert report.ready_for_deployment is False
        assert any("Artifact Purity" in f for f in report.failures)

    def test_docker_build_failure_fails_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        failed_build = BuildResult(success=False, image_tag="test:fail", error="Syntax error")

        report = ClusterModePreflightValidator().evaluate(kit=kit, build_result=failed_build)
        assert report.ready_for_deployment is False
        assert any("Docker Build Gate" in f for f in report.failures)

    def test_runtime_failure_fails_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        failed_runtime = RuntimeResult(root_ok=False, health_ok=False)

        report = ClusterModePreflightValidator().evaluate(kit=kit, runtime_result=failed_runtime)
        assert report.ready_for_deployment is False
        assert any("Runtime Probe Gate" in f for f in report.failures)

    def test_scout_gate_failure_fails_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        failed_scout = ScoutResult(gate_passed=False, critical_count=2, gate_reason="2 Critical CVEs")

        report = ClusterModePreflightValidator().evaluate(kit=kit, scout_result=failed_scout)
        assert report.ready_for_deployment is False
        assert any("Security Scan Gate" in f for f in report.failures)

    def test_scout_unavailable_does_not_fail_preflight(self, valid_fastapi_config):
        kit = DeploymentKitRenderer().render(valid_fastapi_config)
        unavailable_scout = ScoutResult(
            gate_passed=False,
            summary="Docker Scout plugin not available.",
            gate_reason="Docker Scout is not installed or available.",
        )

        report = ClusterModePreflightValidator().evaluate(kit=kit, scout_result=unavailable_scout)
        # Scout unavailable is a warning, not a hard block if build and config pass
        assert report.ready_for_deployment is True
