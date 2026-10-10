"""
Test Matrix for Docker AI (Gordon) Dockerfile Generation & Error Recovery.

Tests:
1. Broken Dockerfile recovery
2. Wrong port recovery
3. Wrong start command recovery
4. Missing dependency recovery
5. Incompatible base image recovery
6. Vulnerable image recovery (Docker Scout critical gate)
7. Docker AI unavailable handling
8. Max repair attempt limits (2-3 attempts)
9. Application source code protection (only Dockerfile modified)
10. Secret protection (.env never leaked)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ebkit.analyzer.scanner import ProjectScanner
from ebkit.commands.init import init_command
from ebkit.generator.docker_ai import DockerAIService, is_docker_ai_available
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.validator.docker_validator import BuildResult, RuntimeResult, ScoutResult


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal valid FastAPI project for testing."""
    proj = tmp_path / "my_project"
    proj.mkdir()
    (proj / "requirements.txt").write_text("fastapi\nuvicorn\n")
    app_dir = proj / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/")\ndef root(): return {"ok": True}\n'
        '@app.get("/health")\ndef health(): return {"status": "healthy"}\n'
    )
    (proj / "Procfile").write_text("web: uvicorn app.main:app --host 0.0.0.0 --port 8080\n")
    return proj


@pytest.fixture
def sample_config() -> DeploymentConfig:
    return DeploymentConfig.model_validate({
        "language": "python",
        "framework": "fastapi",
        "runtime_version": "3.12",
        "package_manager": "pip",
        "dependency_file": "requirements.txt",
        "entrypoint": "app/main.py",
        "port": 8080,
        "start_command": "python -m uvicorn app.main:app --host 0.0.0.0 --port 8080",
        "health_check_path": "/health",
        "platform": "linux/amd64",
        "architecture": "amd64",
        "environment_variables": ["PORT"],
        "uncertainties": [],
    })


# ===========================================================================
# 1. Docker AI Service Core Functionality
# ===========================================================================


class TestDockerAIServiceCore:
    def test_availability_check(self):
        """Service properly inspects docker ai CLI availability."""
        service = DockerAIService()
        assert isinstance(service.is_available(), bool)

    def test_extract_dockerfile_from_code_blocks(self):
        """extract_dockerfile extracts clean instructions from markdown fences."""
        raw = """Here is the Dockerfile:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
EXPOSE 8080
CMD ["python", "app.py"]
```
Let me know if you need changes."""
        extracted = DockerAIService.extract_dockerfile(raw)
        assert extracted is not None
        assert "FROM python:3.12-slim" in extracted
        assert "EXPOSE 8080" in extracted

    def test_extract_diagnosis(self):
        """extract_diagnosis pulls the diagnosis summary from Gordon's answer."""
        raw = """**Diagnosis:** Port 3000 in EXPOSE does not match application port 8080.

```dockerfile
FROM python:3.12-slim
EXPOSE 8080
```"""
        diag = DockerAIService.extract_diagnosis(raw)
        assert "Port 3000 in EXPOSE does not match" in diag

    def test_cluster_mode_safety_enforcement(self, sample_config):
        """_ensure_cluster_mode_safety injects platform flag, non-root user, and matches port."""
        raw_df = "FROM python:3.12\nWORKDIR /app\nCMD [\"python\", \"app.py\"]\n"
        safe_df = DockerAIService._ensure_cluster_mode_safety(raw_df, sample_config)
        assert "--platform=linux/amd64" in safe_df
        assert "USER appuser" in safe_df
        assert "EXPOSE 8080" in safe_df

    def test_sanitize_secrets(self):
        """Secrets and passwords are masked before sending prompt to AI."""
        sensitive_text = "DATABASE_URL=postgres://user:super_secret_password@host/db and token=ghp_123456789012345678901234567890123456"
        sanitized = DockerAIService._sanitize_secrets(sensitive_text)
        assert "super_secret_password" not in sanitized
        assert "token=***" in sanitized


# ===========================================================================
# 2. Docker AI Error Recovery Scenarios
# ===========================================================================


class TestDockerAIRecoveryScenarios:
    def test_broken_dockerfile_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon diagnoses syntax error in broken Dockerfile and rebuild succeeds."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        build_calls = 0

        def fake_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                return BuildResult(success=False, image_tag="test:prod", error="Syntax error: unknown instruction: FOOBAR")
            return BuildResult(success=True, image_tag="test:prod")

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Removed invalid FOOBAR instruction.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", side_effect=fake_build):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", return_value=RuntimeResult(root_ok=True, health_ok=True)):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                    result = runner.invoke(
                                        init_command,
                                        ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                                    )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "🔧 Docker AI (Gordon) diagnosing build failure" in result.output
        assert "Diagnosis: Removed invalid FOOBAR instruction." in result.output
        assert "✓ Dockerfile repaired by Docker AI (Gordon)" in result.output
        assert "✓ Safety validation passed before rebuilding" in result.output
        assert build_calls == 2

    def test_wrong_port_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon diagnoses wrong exposed port and corrects it to match DeploymentConfig."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        runtime_calls = 0

        def fake_runtime(*args, **kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            if runtime_calls == 1:
                return RuntimeResult(root_ok=False, health_ok=False, error="Connection refused on port 8080; container listening on 3000")
            return RuntimeResult(root_ok=True, health_ok=True)

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Corrected EXPOSE and start command port to 8080.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=True, image_tag="test:prod")):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", side_effect=fake_runtime):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                    result = runner.invoke(
                                        init_command,
                                        ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--runtime", "--no-scout"],
                                    )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "🔧 Docker AI (Gordon) diagnosing runtime failure" in result.output
        assert "Corrected EXPOSE and start command port to 8080." in result.output
        assert "✓ Dockerfile repaired by Docker AI (Gordon)" in result.output
        assert runtime_calls == 2

    def test_wrong_start_command_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon diagnoses broken CMD execution and restores proper start command."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        runtime_calls = 0

        def fake_runtime(*args, **kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            if runtime_calls == 1:
                return RuntimeResult(root_ok=False, health_ok=False, error="Process exited with code 127: /bin/sh: invalid_runner: not found")
            return RuntimeResult(root_ok=True, health_ok=True)

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Restored valid uvicorn start command in CMD.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=True, image_tag="test:prod")):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", side_effect=fake_runtime):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                    result = runner.invoke(
                                        init_command,
                                        ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--runtime", "--no-scout"],
                                    )

        assert result.exit_code == 0
        assert "Restored valid uvicorn start command in CMD." in result.output
        assert "✓ Health check passed" in result.output

    def test_missing_dependency_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon diagnoses missing requirements.txt pip install and adds it."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        build_calls = 0

        def fake_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                return BuildResult(success=False, image_tag="test:prod", error="ModuleNotFoundError: No module named 'uvicorn'")
            return BuildResult(success=True, image_tag="test:prod")

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Added missing pip install -r requirements.txt layer.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", side_effect=fake_build):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", return_value=RuntimeResult(root_ok=True, health_ok=True)):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                    result = runner.invoke(
                                        init_command,
                                        ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                                    )

        assert result.exit_code == 0
        assert "Added missing pip install -r requirements.txt" in result.output
        assert build_calls == 2

    def test_incompatible_base_image_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon diagnoses incompatible Alpine glibc base image and updates to Debian slim."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        build_calls = 0

        def fake_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                return BuildResult(success=False, image_tag="test:prod", error="error loading shared library ld-linux-x86-64.so.2 (incompatible alpine musl)")
            return BuildResult(success=True, image_tag="test:prod")

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Replaced Alpine base image with Debian slim for glibc binary compatibility.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", side_effect=fake_build):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", return_value=RuntimeResult(root_ok=True, health_ok=True)):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                    result = runner.invoke(
                                        init_command,
                                        ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                                    )

        assert result.exit_code == 0
        assert "Replaced Alpine base image with Debian slim" in result.output
        assert build_calls == 2

    def test_vulnerable_image_recovery(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Gordon fixes Critical CVEs detected by Docker Scout by updating base image."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        scout_calls = 0

        def fake_scout(*args, **kwargs):
            nonlocal scout_calls
            scout_calls += 1
            if scout_calls == 1:
                return ScoutResult(
                    gate_passed=False,
                    critical_count=2,
                    high_count=1,
                    summary="2 Critical CVEs in outdated base image",
                    gate_reason="Critical CVEs (2) exceed security gate threshold (0).",
                )
            return ScoutResult(
                gate_passed=True,
                critical_count=0,
                high_count=1,
                summary="0 Critical, 1 High CVEs",
                gate_reason="Security gate passed.",
            )

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Updated base image to patched python:3.12-slim to eliminate critical CVEs.")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=True, image_tag="test:prod")):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", return_value=RuntimeResult(root_ok=True, health_ok=True)):
                            with patch("ebkit.validator.docker_validator.DockerScoutValidator.is_scout_available", return_value=True):
                                with patch("ebkit.validator.docker_validator.DockerScoutValidator.scan", side_effect=fake_scout):
                                    with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                            result = runner.invoke(
                                                init_command,
                                                ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--scout"],
                                            )

        assert result.exit_code == 0
        assert "diagnosing security vulnerability and repairing Dockerfile" in result.output
        assert "Updated base image to patched python:3.12-slim" in result.output
        assert "✓ Security gate passed" in result.output
        assert scout_calls == 2


# ===========================================================================
# 3. Constraints, Security & Fallback Tests
# ===========================================================================


class TestDockerAIConstraintsAndSecurity:
    def test_gordon_unavailable_reports_clearly(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """When Gordon is unavailable, EBReady clearly reports it instead of pretending repair worked."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=False):
            with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=False, image_tag="test:prod", error="Build error")):
                    with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                            result = runner.invoke(
                                init_command,
                                ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                            )

        assert result.exit_code != 0
        assert "⚠️ Docker AI (Gordon) is unavailable. Automatic error recovery cannot proceed." in result.output

    def test_max_repair_attempts_limit(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Repair loop halts and fails after maximum repair attempts (default 2)."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Tried repair")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=False, image_tag="test:prod", error="Persistent compiler error")):
                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                result = runner.invoke(
                                    init_command,
                                    ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout", "--max-repair-attempts", "2"],
                                )

        assert result.exit_code != 0
        assert "attempt 1/2" in result.output
        assert "attempt 2/2" in result.output
        assert "Docker build failed after 2 repair attempts" in result.output

    def test_source_code_protected_during_repair(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Recovery only modifies Dockerfile and NEVER modifies application source code files."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        runner = CliRunner()

        original_main = (sample_project / "app" / "main.py").read_text()
        original_reqs = (sample_project / "requirements.txt").read_text()

        build_calls = 0

        def fake_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                return BuildResult(success=False, image_tag="test:prod", error="Dockerfile error")
            return BuildResult(success=True, image_tag="test:prod")

        fixed_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(fixed_df, "Fixed Dockerfile")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", side_effect=fake_build):
                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                runner.invoke(
                                    init_command,
                                    ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                                )

        # Verify application source code was untouched
        assert (sample_project / "app" / "main.py").read_text() == original_main
        assert (sample_project / "requirements.txt").read_text() == original_reqs

    def test_secrets_and_env_files_protected(self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch):
        """Repaired Dockerfile is checked so .env files or secrets are never copied or exposed."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        (sample_project / ".env").write_text("SUPER_SECRET=1234567890\n")

        runner = CliRunner()

        unsafe_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY .env /app/.env\n"  # Secret leak attempt
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(unsafe_df, "Copied env")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=False, image_tag="test:prod", error="Build error")):
                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                            with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                result = runner.invoke(
                                    init_command,
                                    ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--build", "--no-scout"],
                                )

        # CrossFileValidator detects .env in Dockerfile and blocks rebuild
        assert result.exit_code != 0
        assert "Repaired Dockerfile failed safety validation" in result.output

    def test_kept_broken_dockerfile_runs_docker_build_and_recovers(
        self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch
    ):
        """When user chooses 'N' to keep a broken Dockerfile, Docker build still runs, fails, Gordon repairs it, and pipeline succeeds."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        (sample_project / "Dockerfile").write_text("RUN THIS_COMMAND_DOES_NOT_EXIST\n")

        runner = CliRunner()
        build_calls = 0

        def fake_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                return BuildResult(
                    success=False,
                    image_tag="test:prod",
                    error="Error: /bin/sh: THIS_COMMAND_DOES_NOT_EXIST: not found",
                )
            return BuildResult(success=True, image_tag="test:prod")

        repaired_df = (
            "FROM --platform=linux/amd64 python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "USER appuser\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=True):
            with patch("ebkit.generator.docker_ai.DockerAIService.diagnose_and_repair", return_value=(repaired_df, "Removed invalid instruction THIS_COMMAND_DOES_NOT_EXIST")):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                    with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", side_effect=fake_build):
                        with patch("ebkit.validator.docker_validator.DockerRuntimeValidator.validate", return_value=RuntimeResult(root_ok=True, health_ok=True)):
                            with patch("ebkit.validator.docker_validator.DockerScoutValidator.is_scout_available", return_value=True):
                                with patch("ebkit.validator.docker_validator.DockerScoutValidator.scan", return_value=ScoutResult(gate_passed=True, critical_count=0, high_count=0, summary="0 Critical, 0 High")):
                                    with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                                            result = runner.invoke(
                                                init_command,
                                                ["--path", str(sample_project), "--analyzer", "gemini", "--build", "--scout"],
                                                input="N\nN\n",
                                            )

        assert result.exit_code == 0, f"Output:\n{result.output}"
        assert "🐳 Docker Build" in result.output
        assert "✗ Build failed" in result.output
        assert "🤖 Docker AI Recovery" in result.output
        assert "✓ Diagnosis" in result.output
        assert "✓ Safe repair" in result.output
        assert "✓ Build successful" in result.output
        assert "🐳 Runtime" in result.output
        assert "✓ Health check passed" in result.output
        assert "🛡 Docker Scout" in result.output
        assert "✓ Security gate passed" in result.output
        assert "✅ PROJECT READY FOR DEPLOYMENT" in result.output
        assert build_calls == 2

    def test_kept_broken_dockerfile_unrepairable_ends_with_project_not_ready(
        self, sample_project: Path, sample_config: DeploymentConfig, monkeypatch
    ):
        """When Docker build fails on kept Dockerfile and Gordon cannot safely repair it, pipeline ends with ❌ PROJECT NOT READY."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        (sample_project / "Dockerfile").write_text("RUN THIS_COMMAND_DOES_NOT_EXIST\n")

        runner = CliRunner()

        with patch("ebkit.generator.docker_ai.DockerAIService.is_available", return_value=False):
            with patch("ebkit.validator.docker_validator.DockerBuildValidator.is_docker_available", return_value=True):
                with patch("ebkit.validator.docker_validator.DockerBuildValidator.build", return_value=BuildResult(success=False, image_tag="test:prod", error="Error: /bin/sh: THIS_COMMAND_DOES_NOT_EXIST: not found")):
                    with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.__init__", return_value=None):
                        with patch("ebkit.analyzer.ai_analyzer.GoogleAIAnalyzer.analyze", return_value=sample_config):
                            result = runner.invoke(
                                init_command,
                                ["--path", str(sample_project), "--analyzer", "gemini", "--build", "--no-scout"],
                                input="N\nN\n",
                            )

        assert result.exit_code != 0
        assert "🐳 Docker Build" in result.output
        assert "✗ Build failed" in result.output
        assert "❌ PROJECT NOT READY" in result.output
        assert "PROJECT READY FOR DEPLOYMENT" not in result.output
