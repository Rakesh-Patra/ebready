import io
import json
import zipfile
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from ebkit.cli import cli
from ebkit.github_source import public_snapshot

SHA = "a" * 40


def github_responses(monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("repo-commit/Dockerfile", 'FROM python:3.11-slim\nCOPY . .\nCMD ["python", "app.py"]\n')
        bundle.writestr("repo-commit/app.py", 'app.run(host="localhost", port=8080)\n')
        bundle.writestr("repo-commit/.env", "SECRET=must-not-be-copied")
    request = Mock(side_effect=[io.BytesIO(json.dumps({"private": False, "archived": False, "default_branch": "main"}).encode()), io.BytesIO(json.dumps({"sha": SHA}).encode()), io.BytesIO(archive.getvalue())])
    monkeypatch.setattr("ebkit.github_source.urlopen", request)
    return request


def test_download_pins_public_commit_and_reuses_safe_extraction(monkeypatch, tmp_path):
    request = github_responses(monkeypatch)
    result = public_snapshot("https://github.com/owner/repo.git", tmp_path / "snapshot")
    assert result["repository"] == "https://github.com/owner/repo"
    assert result["commit"] == SHA
    assert request.call_args.args[0].full_url == f"https://codeload.github.com/owner/repo/zip/{SHA}"
    assert (tmp_path / "snapshot" / "Dockerfile").exists()
    assert not (tmp_path / "snapshot" / ".env").exists()


@pytest.mark.parametrize("url", ["https://github.com/owner/repo?ref=main", "https://attacker.test/owner/repo", "https://token@github.com/owner/repo", "https://github.com/owner/.."])
def test_rejects_unsupported_urls_before_network(monkeypatch, tmp_path, url):
    request = Mock()
    monkeypatch.setattr("ebkit.github_source.urlopen", request)
    with pytest.raises(ValueError, match="public HTTPS"):
        public_snapshot(url, tmp_path / "snapshot")
    request.assert_not_called()


def test_refuses_nonempty_destination(monkeypatch, tmp_path):
    (tmp_path / "existing.txt").write_text("keep")
    request = Mock()
    monkeypatch.setattr("ebkit.github_source.urlopen", request)
    with pytest.raises(ValueError, match="empty directory"):
        public_snapshot("https://github.com/owner/repo", tmp_path)
    assert (tmp_path / "existing.txt").read_text() == "keep"
    request.assert_not_called()


def test_cli_github_doctor_repairs_retained_snapshot(monkeypatch, tmp_path):
    github_responses(monkeypatch)
    snapshot = tmp_path / "snapshot"
    result = CliRunner().invoke(cli, ["doctor", "https://github.com/owner/repo", "--output", str(snapshot), "--fix", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["source"]["commit"] == SHA
    assert report["verification"]["runtime"] == "not_verified"
    assert 'host="0.0.0.0"' in (snapshot / "app.py").read_text()


def test_cli_requires_destination_before_download(monkeypatch):
    request = Mock()
    monkeypatch.setattr("ebkit.github_source.urlopen", request)
    result = CliRunner().invoke(cli, ["doctor", "https://github.com/owner/repo"])
    assert result.exit_code == 2
    assert "requires --output" in result.output
    request.assert_not_called()


def test_refuses_private_repository_before_downloading_archive(monkeypatch, tmp_path):
    request = Mock(return_value=io.BytesIO(b'{"private":true,"default_branch":"main"}'))
    monkeypatch.setattr("ebkit.github_source.urlopen", request)
    with pytest.raises(ValueError, match="active public"):
        public_snapshot("https://github.com/owner/repo", tmp_path / "snapshot")
    assert request.call_count == 1


def test_caps_archive_download_before_extraction(monkeypatch, tmp_path):
    github_responses(monkeypatch)
    monkeypatch.setattr("ebkit.github_source.MAX_ARCHIVE", 32)
    with pytest.raises(ValueError, match="download limit"):
        public_snapshot("https://github.com/owner/repo", tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
