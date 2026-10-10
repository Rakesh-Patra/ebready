"""
Comprehensive test suite for EBKit Day 2.

Tests:
  1.  FastAPI repository detection
  2.  Node.js repository detection
  3.  DeploymentConfig validation
  4.  Invalid AI output handling
  5.  Jinja2 rendering
  6.  Generated Dockerfile content
  7.  Generated Procfile content
  8.  Existing deployment file safety
  9.  Missing project information handling
  10. Secret/environment variable handling
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Optional

import pytest
from pydantic import ValidationError

from ebkit.analyzer.ai_analyzer import AIAnalyzer
from ebkit.analyzer.scanner import ProjectScanner, ScanResult
from ebkit.generator.renderer import DeploymentKitRenderer
from ebkit.models.deployment_config import (
    DeploymentConfig,
    Language,
    PackageManager,
    Platform,
)


def _derive_config(scan: ScanResult) -> DeploymentConfig:
    """
    Deterministic scan-to-config for testing (replaces removed MockAIAnalyzer).
    Applies simple rule-based logic to produce a valid DeploymentConfig.
    """
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
        "language": lang,
        "framework": framework,
        "runtime_version": runtime_version,
        "package_manager": pm,
        "dependency_file": dep_file,
        "entrypoint": scan.entrypoint,
        "port": port,
        "start_command": start_cmd,
        "health_check_path": health_path,
        "platform": "linux/amd64",
        "architecture": "amd64",
        "container_strategy": container_strategy,
        "eb_deployment_strategy": "docker_single",
        "environment_variables": env_keys,
        "artifacts": {
            "dockerfile": True,
            "dockerignore": True,
            "procfile": True,
            "ebignore": True,
            "env_example": True,
        },
        "uncertainties": [],
    }
    return AIAnalyzer._parse_response(json.dumps(data))


# ---------------------------------------------------------------------------
# Fixtures — synthetic repository directories
# ---------------------------------------------------------------------------


@pytest.fixture()
def fastapi_repo(tmp_path: Path) -> Path:
    """Minimal FastAPI repository."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text(
        textwrap.dedent(
            """\
            import os
            import uvicorn
            from fastapi import FastAPI

            app = FastAPI()
            application = app

            @app.get("/")
            def root():
                return {"service": "ebready"}

            @app.get("/health")
            def health():
                return {"status": "ok"}

            if __name__ == "__main__":
                port = int(os.getenv("PORT", "8080"))
                uvicorn.run("app.main:app", host="0.0.0.0", port=port)
            """
        )
    )
    (tmp_path / "requirements.txt").write_text(
        "fastapi>=0.110.0\nuvicorn[standard]>=0.28.0\n"
    )
    return tmp_path


@pytest.fixture()
def nodejs_repo(tmp_path: Path) -> Path:
    """Minimal Express Node.js repository."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "my-express-app",
                "version": "1.0.0",
                "main": "index.js",
                "scripts": {"start": "node index.js"},
                "dependencies": {"express": "^4.18.0"},
            },
            indent=2,
        )
    )
    (tmp_path / "index.js").write_text(
        textwrap.dedent(
            """\
            const express = require('express');
            const app = express();
            const port = process.env.PORT || 3000;
            app.get('/', (req, res) => res.json({ service: 'my-app' }));
            app.listen(port, () => console.log(`Listening on ${port}`));
            """
        )
    )
    return tmp_path


@pytest.fixture()
def fastapi_with_existing_dockerfile(fastapi_repo: Path) -> Path:
    """FastAPI repo that already has a Dockerfile."""
    (fastapi_repo / "Dockerfile").write_text(
        "FROM python:3.12-slim\nWORKDIR /app\n"
    )
    return fastapi_repo


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    """Repository with no recognisable project files."""
    (tmp_path / "README.md").write_text("# Empty Project\n")
    return tmp_path


@pytest.fixture()
def repo_with_env_file(fastapi_repo: Path) -> Path:
    """FastAPI repo with a real .env file containing secrets."""
    (fastapi_repo / ".env").write_text(
        "SECRET_KEY=super-secret-value\nDATABASE_URL=postgres://user:pass@host/db\n"
    )
    (fastapi_repo / ".env.example").write_text("SECRET_KEY=\nDATABASE_URL=\n")
    return fastapi_repo


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _valid_config(**overrides) -> dict:
    base = {
        "language": "python",
        "framework": "fastapi",
        "runtime_version": "3.12",
        "package_manager": "pip",
        "dependency_file": "requirements.txt",
        "entrypoint": "app/main.py",
        "port": 8080,
        "start_command": "uvicorn app.main:app --host 0.0.0.0 --port 8080",
        "health_check_path": "/health",
        "platform": "linux/amd64",
        "architecture": "amd64",
        "environment_variables": [],
        "uncertainties": [],
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. FastAPI repository detection
# ===========================================================================


class TestFastAPIDetection:
    def test_language_is_python(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.language == "python"

    def test_framework_is_fastapi(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.framework == "fastapi"

    def test_dependency_file_detected(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert "requirements.txt" in scan.dependency_files

    def test_entrypoint_detected(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.entrypoint == "app/main.py"

    def test_port_detected(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.detected_port == 8080

    def test_package_manager_is_pip(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.package_manager == "pip"

    def test_start_command_contains_uvicorn(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.detected_start_command is not None
        assert "uvicorn" in scan.detected_start_command

    def test_no_existing_dockerfile(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.existing_dockerfile is False

    def test_mock_analyzer_produces_valid_config(self, fastapi_repo: Path):
        scan = ProjectScanner(fastapi_repo).scan()
        config = _derive_config(scan)
        assert isinstance(config, DeploymentConfig)
        assert config.language == Language.PYTHON
        assert config.framework == "fastapi"
        assert config.port == 8080


# ===========================================================================
# 2. Node.js repository detection
# ===========================================================================


class TestNodeJSDetection:
    def test_language_is_node(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        assert scan.language == "node"

    def test_framework_is_express(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        assert scan.framework == "express"

    def test_dependency_file_is_package_json(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        assert "package.json" in scan.dependency_files

    def test_package_manager_is_npm(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        assert scan.package_manager == "npm"

    def test_port_detected(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        assert scan.detected_port == 3000

    def test_mock_analyzer_node(self, nodejs_repo: Path):
        scan = ProjectScanner(nodejs_repo).scan()
        config = _derive_config(scan)
        assert config.language == Language.NODE


# ===========================================================================
# 3. DeploymentConfig validation
# ===========================================================================


class TestDeploymentConfigValidation:
    def test_valid_config_accepted(self):
        cfg = DeploymentConfig.model_validate(_valid_config())
        assert cfg.language == Language.PYTHON
        assert cfg.port == 8080

    def test_port_must_be_in_range(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(_valid_config(port=0))

        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(_valid_config(port=99999))

    def test_health_check_must_start_with_slash(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(_valid_config(health_check_path="health"))

    def test_framework_is_lowercased(self):
        cfg = DeploymentConfig.model_validate(_valid_config(framework="FastAPI"))
        assert cfg.framework == "fastapi"

    def test_platform_architecture_auto_corrected(self):
        # arm64 platform but amd64 architecture — should auto-correct
        cfg = DeploymentConfig.model_validate(
            _valid_config(platform="linux/arm64", architecture="amd64")
        )
        assert cfg.architecture == "arm64"

    def test_base_image_python(self):
        cfg = DeploymentConfig.model_validate(_valid_config())
        assert cfg.base_image() == "python:3.12-slim"

    def test_base_image_node(self):
        cfg = DeploymentConfig.model_validate(
            _valid_config(
                language="node",
                runtime_version="20",
                start_command="node index.js",
            )
        )
        assert cfg.base_image() == "node:20-alpine"

    def test_runtime_version_invalid_string_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(_valid_config(runtime_version="not-a-version!!!"))

    def test_runtime_version_none_accepted(self):
        cfg = DeploymentConfig.model_validate(_valid_config(runtime_version=None))
        assert cfg.runtime_version is None

    def test_uncertainties_stored(self):
        cfg = DeploymentConfig.model_validate(
            _valid_config(
                uncertainties=[{"field_name": "port", "reason": "not found"}]
            )
        )
        assert cfg.is_uncertain()
        assert cfg.uncertainties[0].field_name == "port"


# ===========================================================================
# 4. Invalid AI output handling
# ===========================================================================


class TestInvalidAIOutput:
    def test_shell_expansion_in_start_command_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(
                _valid_config(start_command="uvicorn app:app --port $(cat /etc/passwd)")
            )

    def test_backtick_in_start_command_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(
                _valid_config(start_command="uvicorn app:app --port `id`")
            )

    def test_env_var_with_value_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(
                _valid_config(environment_variables=["PORT=8080"])
            )

    def test_invalid_json_from_ai_raises_value_error(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            AIAnalyzer._parse_response("This is not JSON at all.")

    def test_markdown_fenced_json_is_stripped_and_parsed(self):
        raw = '```json\n' + json.dumps(_valid_config()) + '\n```'
        cfg = AIAnalyzer._parse_response(raw)
        assert cfg.language == Language.PYTHON

    def test_schema_violation_from_ai_raises_value_error(self):
        bad = _valid_config()
        bad["port"] = "not-a-number"
        with pytest.raises(ValueError, match="schema validation"):
            AIAnalyzer._parse_response(json.dumps(bad))

    def test_unknown_language_accepted(self):
        cfg = DeploymentConfig.model_validate(
            _valid_config(language="unknown", start_command="./start.sh")
        )
        assert cfg.language == Language.UNKNOWN


# ===========================================================================
# 5. Jinja2 rendering
# ===========================================================================


class TestJinja2Rendering:
    @pytest.fixture()
    def config(self) -> DeploymentConfig:
        return DeploymentConfig.model_validate(_valid_config())

    @pytest.fixture()
    def kit(self, config: DeploymentConfig):
        return DeploymentKitRenderer().render(config)

    def test_all_files_rendered(self, kit):
        expected = {"Dockerfile", ".dockerignore", "Procfile", ".ebignore", ".env.example"}
        assert set(kit.files.keys()) == expected

    def test_rendered_files_are_non_empty(self, kit):
        for fname, content in kit.files.items():
            assert content.strip(), f"{fname} rendered to empty content"


# ===========================================================================
# 6. Generated Dockerfile
# ===========================================================================


class TestGeneratedDockerfile:
    def _dockerfile(self, **overrides) -> str:
        cfg = DeploymentConfig.model_validate(_valid_config(**overrides))
        return DeploymentKitRenderer().render(cfg).files["Dockerfile"]

    def test_contains_from_instruction(self):
        content = self._dockerfile()
        assert content.startswith("# syntax=docker/dockerfile:1") or "FROM" in content

    def test_base_image_matches_config(self):
        content = self._dockerfile()
        assert "python:3.12-slim" in content

    def test_expose_port_present(self):
        content = self._dockerfile()
        assert "EXPOSE 8080" in content

    def test_start_command_in_cmd(self):
        content = self._dockerfile()
        assert "uvicorn" in content

    def test_node_dockerfile_uses_node_image(self):
        content = self._dockerfile(
            language="node",
            runtime_version="20",
            package_manager="npm",
            dependency_file="package.json",
            start_command="node index.js",
            port=3000,
        )
        assert "node:20-alpine" in content
        assert "EXPOSE 3000" in content

    def test_platform_annotation_present(self):
        content = self._dockerfile()
        assert "linux/amd64" in content

    def test_pip_install_command_present(self):
        content = self._dockerfile()
        assert "pip install" in content


# ===========================================================================
# 7. Generated Procfile
# ===========================================================================


class TestGeneratedProcfile:
    def _procfile(self, **overrides) -> str:
        cfg = DeploymentConfig.model_validate(_valid_config(**overrides))
        return DeploymentKitRenderer().render(cfg).files["Procfile"]

    def test_web_process_defined(self):
        content = self._procfile()
        # Template has a comment header — find any non-comment line starting with web:
        process_lines = [l for l in content.splitlines() if l.strip() and not l.startswith("#")]
        assert any(l.startswith("web:") for l in process_lines)

    def test_start_command_in_procfile(self):
        content = self._procfile()
        assert "uvicorn app.main:app" in content

    def test_procfile_single_line(self):
        content = self._procfile()
        # Strip comment lines, should have only one actual process
        process_lines = [l for l in content.splitlines() if l.strip() and not l.startswith("#")]
        assert len(process_lines) == 1


# ===========================================================================
# 8. Existing deployment file safety
# ===========================================================================


class TestExistingFileSafety:
    def test_existing_dockerfile_detected(self, fastapi_with_existing_dockerfile: Path):
        scan = ProjectScanner(fastapi_with_existing_dockerfile).scan()
        assert scan.existing_dockerfile is True

    def test_existing_dockerfile_note_present(self, fastapi_with_existing_dockerfile: Path):
        scan = ProjectScanner(fastapi_with_existing_dockerfile).scan()
        assert any("Dockerfile" in note for note in scan.notes)

    def test_existing_procfile_detected(self, fastapi_repo: Path):
        (fastapi_repo / "Procfile").write_text("web: uvicorn app.main:app\n")
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.existing_procfile is True

    def test_existing_ebextensions_detected(self, fastapi_repo: Path):
        (fastapi_repo / ".ebextensions").mkdir()
        (fastapi_repo / ".ebextensions" / "config.yml").write_text("option_settings: {}\n")
        scan = ProjectScanner(fastapi_repo).scan()
        assert scan.existing_ebextensions is True


# ===========================================================================
# 9. Missing project information handling
# ===========================================================================


class TestMissingProjectInfo:
    def test_empty_repo_has_unknown_language(self, empty_repo: Path):
        scan = ProjectScanner(empty_repo).scan()
        assert scan.language is None

    def test_empty_repo_notes_explain_issue(self, empty_repo: Path):
        scan = ProjectScanner(empty_repo).scan()
        assert len(scan.notes) > 0

    def test_mock_analyzer_handles_missing_language(self, empty_repo: Path):
        scan = ProjectScanner(empty_repo).scan()
        # MockAIAnalyzer should not raise — it defaults gracefully
        config = _derive_config(scan)
        assert config.language == Language.UNKNOWN

    def test_config_with_no_uncertainty_is_not_uncertain(self):
        cfg = DeploymentConfig.model_validate(_valid_config())
        assert cfg.is_uncertain() is False

    def test_missing_optional_fields_use_defaults(self):
        minimal = {
            "language": "python",
            "start_command": "uvicorn main:app --host 0.0.0.0 --port 8080",
            "port": 8080,
            "package_manager": "pip",
            "platform": "linux/amd64",
            "architecture": "amd64",
        }
        cfg = DeploymentConfig.model_validate(minimal)
        assert cfg.health_check_path == "/health"
        assert cfg.environment_variables == []


# ===========================================================================
# 10. Secret / environment variable handling
# ===========================================================================


class TestSecretHandling:
    def test_env_file_detected_but_not_read(self, repo_with_env_file: Path):
        scan = ProjectScanner(repo_with_env_file).scan()
        assert ".env" in scan.env_files

    def test_env_file_values_not_in_scan_result(self, repo_with_env_file: Path):
        scan = ProjectScanner(repo_with_env_file).scan()
        # The scan result should not contain actual secret values
        scan_dict_str = json.dumps(scan.as_dict())
        assert "super-secret-value" not in scan_dict_str
        assert "postgres://user:pass" not in scan_dict_str

    def test_env_example_does_not_contain_values(self, repo_with_env_file: Path):
        scan = ProjectScanner(repo_with_env_file).scan()
        config = _derive_config(scan)
        kit = DeploymentKitRenderer().render(config)
        env_example = kit.files[".env.example"]
        # Should never have values — only keys
        assert "super-secret-value" not in env_example
        assert "postgres://user:pass" not in env_example

    def test_env_vars_in_config_are_keys_only(self):
        cfg = DeploymentConfig.model_validate(
            _valid_config(environment_variables=["SECRET_KEY", "DATABASE_URL", "PORT"])
        )
        for key in cfg.environment_variables:
            assert "=" not in key

    def test_ebignore_excludes_env_files(self):
        cfg = DeploymentConfig.model_validate(_valid_config())
        kit = DeploymentKitRenderer().render(cfg)
        ebignore = kit.files[".ebignore"]
        assert ".env" in ebignore

    def test_env_var_with_assignment_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentConfig.model_validate(
                _valid_config(environment_variables=["SECRET_KEY=mysecret"])
            )


# ===========================================================================
# Golden test: full pipeline with the real EBReady FastAPI project
# ===========================================================================


class TestGoldenFastAPIProject:
    """End-to-end test simulating an EBReady FastAPI hello-world project."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (tmp_path / "requirements.txt").write_text(
            "fastapi\nuvicorn\n",
            encoding="utf-8",
        )
        (tmp_path / "Procfile").write_text(
            "web: python -m uvicorn app.main:app --host 0.0.0.0 --port 8080\n",
            encoding="utf-8",
        )
        (tmp_path / "Dockerfile").write_text(
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 8080\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", "
            "\"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n",
            encoding="utf-8",
        )
        (app_dir / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            '@app.get("/health")\n'
            "def health(): return {\"status\": \"ok\"}\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_scanner_detects_fastapi(self, project: Path):
        scan = ProjectScanner(project).scan()
        assert scan.language == "python"
        assert scan.framework == "fastapi"
        assert "requirements.txt" in scan.dependency_files
        assert scan.entrypoint == "app/main.py"

    def test_scanner_detects_existing_dockerfile(self, project: Path):
        scan = ProjectScanner(project).scan()
        assert scan.existing_dockerfile is True

    def test_full_pipeline_produces_valid_config(self, project: Path):
        scan = ProjectScanner(project).scan()
        config = _derive_config(scan)
        assert isinstance(config, DeploymentConfig)
        assert config.port == 8080
        assert config.language == Language.PYTHON
        assert config.architecture == "amd64"
        assert config.health_check_path == "/health"
        assert config.dependency_file == "requirements.txt"

    def test_full_pipeline_renders_all_files(self, project: Path):
        scan = ProjectScanner(project).scan()
        config = _derive_config(scan)
        kit = DeploymentKitRenderer().render(config)
        assert "Dockerfile" in kit.files
        assert "Procfile" in kit.files
        assert ".ebignore" in kit.files
        assert ".env.example" in kit.files
        # Cluster Mode does NOT generate .ebextensions
        assert ".ebextensions/config.yml" not in kit.files

    def test_generated_dockerfile_correct_image(self, project: Path):
        scan = ProjectScanner(project).scan()
        config = _derive_config(scan)
        kit = DeploymentKitRenderer().render(config)
        dockerfile = kit.files["Dockerfile"]
        assert "python:" in dockerfile
        assert "slim" in dockerfile

    def test_generated_procfile_correct(self, project: Path):
        scan = ProjectScanner(project).scan()
        config = _derive_config(scan)
        kit = DeploymentKitRenderer().render(config)
        procfile = kit.files["Procfile"]
        process_lines = [l for l in procfile.splitlines() if l.strip() and not l.startswith("#")]
        assert any(l.startswith("web:") for l in process_lines)
        assert "uvicorn" in procfile

    def test_scan_result_serialisable(self, project: Path):
        scan = ProjectScanner(project).scan()
        d = scan.as_dict()
        # Should be JSON-serialisable
        json.dumps(d)
