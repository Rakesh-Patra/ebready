"""
Comprehensive Test Matrix for EBReady Interactive CLI UX, Configuration, and Security.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ebkit.analyzer.ai_analyzer import (
    ClaudeAIAnalyzer,
    GoogleAIAnalyzer,
    GroqAIAnalyzer,
    OpenAIAnalyzer,
    get_analyzer,
)
from ebkit.analyzer.scanner import ProjectScanner
from ebkit.commands.init import init_command
from ebkit.config import EBKitConfig, load_config, save_config
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.repo_handler import safe_clone_repo, validate_github_url


def _create_sample_config() -> DeploymentConfig:
    """Create a deterministic DeploymentConfig for testing (replaces MockAIAnalyzer)."""
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


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal valid FastAPI project for testing CLI."""
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
    return proj


# ===========================================================================
# 1. Interactive CLI Flow Tests
# ===========================================================================


class TestInteractiveCLI:
    def test_interactive_current_directory_with_gemini(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Option 1 selects current directory and Option 1 selects Gemini (Available)."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-12345")

        dummy_config = _create_sample_config()

        runner = CliRunner()
        # Inputs:
        # Option 1 (Current directory)
        # Option 1 (Gemini)
        user_input = "1\n1\n"
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--no-build", "--port", "8080"],
                    input=user_input,
                )

        assert result.exit_code == 0, f"Error: {result.output}"
        assert "🚀 Welcome to EBReady" in result.output
        assert "Where is your project?" in result.output
        assert "Project path: ." in result.output
        assert "1. Gemini — ✅ Available" in result.output
        assert "2. OpenAI — 🔜 Coming Later" in result.output
        assert "3. Claude — 🔜 Coming Later" in result.output
        assert "4. Groq API — 🔜 Coming Later" in result.output
        assert "🤖 Running Gemini analysis..." in result.output
        assert "📦 Generating deployment kit..." in result.output
        assert "✓ Dockerfile" in result.output
        assert "PROJECT READY FOR DEPLOYMENT" in result.output
        # No AWS prompts
        assert "AWS Region" not in result.output
        assert "Elastic Beanstalk Application" not in result.output
        assert "Elastic Beanstalk Environment" not in result.output

        saved = load_config(config_file)
        assert saved is not None
        assert saved.ai_provider == "gemini"

    def test_interactive_local_path(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Option 3 prompts for local path and validates it."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        dummy_config = _create_sample_config()

        runner = CliRunner()
        # Inputs: Option 3 -> local path -> Gemini (1)
        user_input = f"3\n{sample_project}\n1\n"
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--no-build", "--port", "8080"],
                    input=user_input,
                )

        assert result.exit_code == 0, f"Error: {result.output}"
        assert "Local project path:" in result.output
        assert "PROJECT READY FOR DEPLOYMENT" in result.output

        saved = load_config(config_file)
        assert saved is not None
        assert saved.ai_provider == "gemini"

    def test_init_generates_root_dockerfile_for_multitier_project(
        self, sample_project: Path, monkeypatch, tmp_path: Path
    ):
        (sample_project / "frontend").mkdir()
        (sample_project / "backend").mkdir()
        (sample_project / "docker-compose.yml").write_text(
            "services:\n"
            "  postgres:\n"
            "    image: postgres:16-alpine\n"
            "    ports:\n"
            '      - "5432:5432"\n'
            "  backend:\n"
            "    build: ./backend\n"
            "    environment:\n"
            "      DATABASE_URL: ${DATABASE_URL}\n"
            "    ports:\n"
            '      - "8081:8080"\n'
            "  frontend:\n"
            "    build: ./frontend\n"
            "    ports:\n"
            '      - "${FRONTEND_HOST_PORT:-8080}:4173"\n',
            encoding="utf-8",
        )
        (sample_project / ".env").write_text(
            "DATABASE_URL=postgres://existing-secret.example/app\n",
            encoding="utf-8",
        )
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        dummy_config = _create_sample_config()

        def generate_multitier_dockerfile(config, _scan, **_kwargs):
            return (
                "FROM --platform=linux/amd64 python:3.12-slim\n"
                "WORKDIR /app\n"
                "COPY requirements.txt ./\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\n"
                f"EXPOSE {config.port}\n"
                "USER nobody\n"
                f'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "{config.port}"]\n',
                "generated for test",
            )

        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                with patch(
                    "ebkit.commands.init.DockerAIService.is_available",
                    return_value=False,
                ):
                    with patch(
                        "ebkit.commands.init.DockerAIService.generate_dockerfile",
                        side_effect=generate_multitier_dockerfile,
                    ):
                        result = CliRunner().invoke(
                            init_command,
                            [
                                "--path",
                                str(sample_project),
                                "--analyzer",
                                "gemini",
                                "--yes",
                                "--no-build",
                            ],
                        )

        assert result.exit_code == 0, result.output
        assert "AI to generate and validate one root-level Dockerfile" in result.output
        assert "Compose stateful services detected: postgres" in result.output
        assert "Provision the required database/cache/broker separately" in result.output
        assert "Port 4173 detected" in result.output
        assert "ebkit deploy https://github.com/OWNER/REPOSITORY.git" in result.output
        assert "--env-file .env" in result.output
        assert "existing-secret.example" not in result.output
        dockerfile = (sample_project / "Dockerfile").read_text(encoding="utf-8")
        assert "EXPOSE 4173" in dockerfile
        env_example = (sample_project / ".env.example").read_text(encoding="utf-8")
        assert "DATABASE_URL=" in env_example
        assert "FRONTEND_HOST_PORT=" in env_example
        assert "postgres://db:" not in env_example
        env_file = (sample_project / ".env").read_text(encoding="utf-8")
        assert "DATABASE_URL=postgres://existing-secret.example/app" in env_file
        assert "FRONTEND_HOST_PORT=" in env_file
        assert ".env" in (sample_project / ".gitignore").read_text(encoding="utf-8")

    def test_interactive_github_repo(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Option 2 retains the clone and writes a complete Dockerfile at its root."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.chdir(tmp_path)

        dummy_config = _create_sample_config()

        runner = CliRunner()
        github_url = "https://github.com/test-owner/test-repo"

        def clone_into_destination(url: str, *, dest_dir: Path) -> Path:
            shutil.copytree(sample_project, dest_dir, dirs_exist_ok=True)
            return dest_dir

        with patch("ebkit.commands.init.safe_clone_repo", side_effect=clone_into_destination) as mock_clone:
            with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
                with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                    # Inputs: Option 2 -> github_url -> Gemini (1)
                    user_input = f"2\n{github_url}\n1\n"
                    result = runner.invoke(
                        init_command,
                        ["--no-build", "--port", "8080"],
                        input=user_input,
                    )

                    assert result.exit_code == 0, f"Error: {result.output}"
                    assert "GitHub repository URL:" in result.output
                    assert f"Cloning repository into {tmp_path / 'test-repo'}" in result.output
                    assert "PROJECT READY FOR DEPLOYMENT" in result.output
                    mock_clone.assert_called_once_with(
                        github_url,
                        dest_dir=tmp_path / "test-repo",
                    )
                    dockerfile = tmp_path / "test-repo" / "Dockerfile"
                    assert dockerfile.is_file()
                    dockerfile_content = dockerfile.read_text(encoding="utf-8")
                    assert "FROM " in dockerfile_content
                    assert "EXPOSE 8080" in dockerfile_content
                    assert "CMD " in dockerfile_content

    def test_github_repo_clone_does_not_overwrite_existing_folder(self, monkeypatch, tmp_path: Path):
        """GitHub init refuses to use an existing repository-named directory."""
        monkeypatch.chdir(tmp_path)
        destination = tmp_path / "test-repo"
        destination.mkdir()
        existing_file = destination / "keep.txt"
        existing_file.write_text("keep", encoding="utf-8")

        runner = CliRunner()
        with patch("ebkit.commands.init.safe_clone_repo") as mock_clone:
            result = runner.invoke(
                init_command,
                ["--repo", "https://github.com/test-owner/test-repo", "--yes"],
            )

        assert result.exit_code != 0
        assert "already exists" in result.output
        assert existing_file.read_text(encoding="utf-8") == "keep"
        mock_clone.assert_not_called()

    def test_openai_coming_later(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Selecting Option 2 (OpenAI) outputs Coming Later and exits."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="1\n2\n")
        assert result.exit_code != 0
        assert "OpenAI is coming later" in result.output

    def test_claude_coming_later(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Selecting Option 3 (Claude) outputs Coming Later and exits."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="1\n3\n")
        assert result.exit_code != 0
        assert "Claude is coming later" in result.output

    def test_groq_coming_later(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Selecting Option 4 (Groq) outputs Coming Later and exits."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="1\n4\n")
        assert result.exit_code != 0
        assert "Groq API is coming later" in result.output

    def test_invalid_ai_provider_selection(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Selecting invalid provider number exits with clear error."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="1\n5\n")
        assert result.exit_code != 0
        assert "❌ Invalid AI provider selection: '5'. Please select 1, 2, 3, or 4." in result.output

    def test_cli_analyzer_coming_later_flags(self, sample_project: Path):
        """Specifying --analyzer with upcoming providers displays Coming Later."""
        runner = CliRunner()
        for prov in ("openai", "claude", "groq"):
            res = runner.invoke(init_command, ["--path", str(sample_project), "--analyzer", prov, "--yes"])
            assert res.exit_code != 0
            assert "is coming later. Currently, only Gemini is available." in res.output

    def test_invalid_source_selection(self, monkeypatch, tmp_path: Path):
        """Entering invalid option for project source exits with clear error."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="9\n")
        assert result.exit_code != 0
        assert "❌ Invalid selection: '9'. Please select 1, 2, or 3." in result.output

    def test_invalid_local_path(self, monkeypatch, tmp_path: Path):
        """Non-existent local project path exits with clear error."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        bad_path = str(tmp_path / "non_existent_dir_12345")
        result = runner.invoke(init_command, input=f"3\n{bad_path}\n")
        assert result.exit_code != 0
        assert "❌ Project path does not exist:" in result.output

    def test_invalid_github_url(self, monkeypatch, tmp_path: Path):
        """Invalid GitHub URL exits with clear error."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        runner = CliRunner()
        result = runner.invoke(init_command, input="2\nhttps://evil-site.com/exploit\n")
        assert result.exit_code != 0
        assert "❌ Invalid GitHub repository URL:" in result.output

    def test_missing_gemini_key(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Selecting Gemini when GEMINI_API_KEY is missing prints clear message and exits."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        runner = CliRunner()
        # Option 1 (current dir) -> Option 1 (Gemini)
        result = runner.invoke(init_command, input="1\n1\n")
        assert result.exit_code != 0
        assert "❌ GEMINI_API_KEY is not set." in result.output
        assert "Set it in your environment and run `ebkit init` again." in result.output

    def test_default_values(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Pressing enter uses all sensible defaults (default provider is Gemini)."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        dummy_config = _create_sample_config()

        runner = CliRunner()
        # Option 1 (default) -> Option 1 (default Gemini)
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--no-build", "--port", "8080"],
                    input="\n\n",
                )
                assert result.exit_code == 0, f"Error: {result.output}"
                assert "Project path: ." in result.output

        saved = load_config(config_file)
        assert saved is not None
        assert saved.ai_provider == "gemini"

    def test_saved_configuration_flow(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Subsequent run detects saved configuration and offers to reuse it."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        initial_cfg = EBKitConfig(
            ai_provider="gemini",
        )
        save_config(initial_cfg, config_file)

        dummy_config = _create_sample_config()

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                # Option 1 (current dir) -> Continue with saved config? "y"
                result = runner.invoke(
                    init_command,
                    ["--no-build", "--port", "8080"],
                    input="1\ny\n",
                )
                assert result.exit_code == 0, f"Error: {result.output}"
                assert "Using saved configuration:" in result.output
                assert "AI Provider: Gemini" in result.output
                assert "Continue? [Y/n]" in result.output
                assert "PROJECT READY FOR DEPLOYMENT" in result.output

    def test_changing_saved_configuration(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """User can decline saved configuration and modify preferences."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        initial_cfg = EBKitConfig(
            ai_provider="gemini",
        )
        save_config(initial_cfg, config_file)

        dummy_config = _create_sample_config()

        runner = CliRunner()
        # Inputs:
        # Option 1 (current dir)
        # Continue? "n"
        # AI Provider? "1" (Gemini)
        user_input = "1\nn\n1\n"
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--no-build", "--port", "8080"],
                    input=user_input,
                )

        assert result.exit_code == 0, f"Error: {result.output}"
        updated_cfg = load_config(config_file)
        assert updated_cfg is not None
        assert updated_cfg.ai_provider == "gemini"

    def test_non_interactive_missing_source_fails(self):
        """Non-interactive mode without path or repo fails with clear error message."""
        runner = CliRunner()
        result = runner.invoke(init_command, ["--yes"])
        assert result.exit_code != 0
        assert "❌ Non-interactive mode requires a project path or repository." in result.output

    def test_non_interactive_with_path_succeeds(self, sample_project: Path, tmp_path: Path, monkeypatch):
        """Non-interactive mode with path and Gemini analyzer succeeds without prompts."""
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        dummy_config = _create_sample_config()

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(
                    init_command,
                    ["--path", str(sample_project), "--analyzer", "gemini", "--yes", "--no-build"],
                )
        assert result.exit_code == 0, f"Error: {result.output}"
        assert "PROJECT READY FOR DEPLOYMENT" in result.output


# ===========================================================================
# 2. Architecture & Extensibility Tests
# ===========================================================================


class TestAIProviderArchitecture:
    def test_provider_stubs_raise_not_implemented(self):
        """Extensible stubs for upcoming providers cleanly raise NotImplementedError."""
        with pytest.raises(NotImplementedError, match="OpenAI provider is coming later"):
            OpenAIAnalyzer()

        with pytest.raises(NotImplementedError, match="Claude provider is coming later"):
            ClaudeAIAnalyzer()

        with pytest.raises(NotImplementedError, match="Groq API provider is coming later"):
            GroqAIAnalyzer()

    def test_no_silent_fallback(self, monkeypatch):
        """get_analyzer must not silently fall back when no key is set."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Calling get_analyzer without keys attempts Gemini (GoogleAIAnalyzer), raising EnvironmentError
        with pytest.raises(EnvironmentError, match="Set GOOGLE_API_KEY or GEMINI_API_KEY"):
            get_analyzer()


# ===========================================================================
# 3. Security Tests
# ===========================================================================


class TestSecurity:
    def test_api_keys_never_printed(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """Sensitive API key values are never printed in CLI output."""
        monkeypatch.chdir(sample_project)
        secret_key = "super-secret-gemini-key-xyz-987"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))

        dummy_config = _create_sample_config()

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
            with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
                result = runner.invoke(init_command, ["--no-build"], input="1\n1\n")
                assert secret_key not in result.output

    def test_api_keys_never_stored_in_config(self, tmp_path: Path):
        """Config manager refuses to store any sensitive keys."""
        config_file = tmp_path / "config"
        cfg = EBKitConfig(ai_provider="gemini")

        # Saving valid non-secret config succeeds
        save_config(cfg, config_file)
        content = config_file.read_text()
        assert "GEMINI_API_KEY" not in content
        assert "AWS_SECRET_ACCESS_KEY" not in content

        # Directly verifying config dict enforcement
        with pytest.raises(ValueError, match="Disallowed configuration key"):
            bad_data = cfg.as_dict()
            bad_data["GEMINI_API_KEY"] = "secret"
            from ebkit.config import save_config as raw_save
            with patch.object(cfg, "as_dict", return_value=bad_data):
                raw_save(cfg, config_file)

    def test_aws_credentials_never_requested(self, sample_project: Path, monkeypatch, tmp_path: Path):
        """EBReady asks only for project source and AI provider — never AWS secret keys."""
        monkeypatch.chdir(sample_project)
        config_file = tmp_path / "config"
        monkeypatch.setenv("EBKIT_CONFIG_FILE", str(config_file))
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        dummy_config = _create_sample_config()

        runner = CliRunner()
        with patch.object(GoogleAIAnalyzer, "__init__", return_value=None):
            with patch.object(GoogleAIAnalyzer, "analyze", return_value=dummy_config):
                result = runner.invoke(init_command, ["--no-build"], input="1\n1\n")
                assert "AWS Secret" not in result.output
                assert "Access Key" not in result.output
                assert "Security Token" not in result.output
                assert "AWS_SECRET_ACCESS_KEY" not in result.output
                assert "AWS Region" not in result.output
                assert "Elastic Beanstalk Application" not in result.output
                assert "Elastic Beanstalk Environment" not in result.output

    @pytest.mark.parametrize(
        "unsafe_url",
        [
            "--upload-pack=exploit",
            "-o/tmp/pwn",
            "https://github.com/user/repo;rm -rf /",
            "https://github.com/user/repo|cat /etc/passwd",
            "https://github.com/user/repo`whoami`",
            "file:///etc/passwd",
            "ssh://git@github.com/user/repo",
            "ftp://github.com/user/repo",
            "https://user:password@github.com/user/repo",
            "https://evil.com/user/repo",
            "https://github.com/../../etc",
            "https://github.com/invalid_repo",
        ],
    )
    def test_unsafe_github_urls_rejected(self, unsafe_url: str):
        """Unsafe or malicious URLs are strictly rejected by validate_github_url."""
        is_valid, err_msg = validate_github_url(unsafe_url)
        assert is_valid is False
        assert len(err_msg) > 0

    def test_safe_github_urls_accepted(self):
        """Standard valid GitHub URLs are accepted."""
        valid_urls = [
            "https://github.com/fastapi/fastapi",
            "https://github.com/owner/my-app",
            "https://github.com/owner/my-app.git",
            "http://github.com/owner/my-app",
        ]
        for url in valid_urls:
            is_valid, err = validate_github_url(url)
            assert is_valid is True, f"Failed for {url}: {err}"

    def test_repository_code_not_executed(self, tmp_path: Path):
        """Scanning a repository never executes arbitrary python code in it."""
        malicious_repo = tmp_path / "malicious"
        malicious_repo.mkdir()
        (malicious_repo / "requirements.txt").write_text("fastapi\n")
        (malicious_repo / "app.py").write_text(
            'raise RuntimeError("MALICIOUS CODE EXECUTED!")\n'
        )

        scanner = ProjectScanner(malicious_repo)
        scan = scanner.scan()
        assert scan.language == "python"
