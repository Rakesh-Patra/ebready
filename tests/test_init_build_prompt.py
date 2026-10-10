import click
import pytest
from click.testing import CliRunner

from ebkit.commands.init import _choose_local_build
from ebkit.commands.init import _show_source_dependency_fixes
from ebkit.commands.init import _print_stateful_service_deploy_guidance
from ebkit.validator.docker_validator import ScoutResult


def test_deploy_guidance_lists_settings_without_reading_secrets(tmp_path, capsys):
    (tmp_path / ".env").write_text("POSTGRES_URL=private-secret\n")
    _print_stateful_service_deploy_guidance(
        tmp_path, "https://github.com/Rakesh-Patra/devboard.git", 8080,
        ["POSTGRES_URL", "PORT", "API_KEY"], {"POSTGRES_URL"},
    )
    output = capsys.readouterr().out
    assert "POSTGRES_URL=<YOUR_POSTGRES_URL>" in output
    assert "API_KEY=<YOUR_API_KEY>" in output
    assert '--env "POSTGRES_URL=<YOUR_POSTGRES_URL>"' in output
    assert "--env-file .env" in output
    assert "PORT=8080" in output
    assert "private-secret" not in output


def test_source_failure_explains_dependency_fixes(capsys):
    result = ScoutResult(gate_passed=False, details=[{
        "id": "CVE-example", "packages": ["pkg:npm/example@1.0"],
        "fixed_version": "1.1", "paths": ["frontend/package-lock.json"],
    }])
    _show_source_dependency_fixes(result)
    output = capsys.readouterr().out
    assert "frontend/package-lock.json" in output
    assert "Reported fixed versions: 1.1" in output
    assert "CVE-example" in output
    assert "Fixed version: 1.1" in output
    assert "security gate remains enforced" in output
    assert result.gate_passed is False


@pytest.mark.parametrize("answer,expected", [("\n", False), ("n\n", False), ("y\n", True)])
def test_interactive_build_choice(answer, expected):
    @click.command()
    def command():
        click.echo(f"build={_choose_local_build(None, None, False)}")

    result = CliRunner().invoke(command, input=answer)
    assert result.exit_code == 0
    assert "Build the Docker image locally? [y/N]" in result.output
    assert f"build={expected}" in result.output


@pytest.mark.parametrize("build,runtime,yes,expected", [
    (None, None, True, False), (True, None, False, True),
    (False, None, False, False), (None, True, False, True),
])
def test_explicit_build_choices_skip_prompt(monkeypatch, build, runtime, yes, expected):
    def unexpected_prompt(*args, **kwargs):
        pytest.fail("Explicit choices must skip the build prompt")

    monkeypatch.setattr(click, "confirm", unexpected_prompt)
    assert _choose_local_build(build, runtime, yes) is expected
