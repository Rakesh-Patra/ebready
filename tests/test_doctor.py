import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from ebkit.cli import cli
from ebkit.doctor import analyze, apply_repairs
from ebkit.commands.doctor import verify_project


def project(tmp_path):
    (tmp_path / "Dockerfile").write_text('FROM node:18-alpine\nCOPY . .\nEXPOSE 8000\nCMD ["npm", "start"]\n')
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"vite": "^7.0.0"}}))
    (tmp_path / "app.py").write_text('import uvicorn\nDATABASE_URL = "http://localhost:5432"\nuvicorn.run("app:app", host="127.0.0.1", port=8080)\n')
    return tmp_path


def test_review_and_apply_repairs_without_changing_database_host(tmp_path):
    root = project(tmp_path)
    plan = analyze(root, 8080)
    assert {f["code"] for f in plan["findings"]} >= {"node-runtime", "exposed-port", "loopback-binding"}
    assert 'FROM node:18-alpine' in (root / "Dockerfile").read_text()
    result = apply_repairs(root, plan)
    assert set(result["applied"]) == {"Dockerfile", "app.py"}
    assert result["findings"] == []
    assert result["verification"]["runtime"] == "not_verified"
    assert "FROM node:22-alpine" in (root / "Dockerfile").read_text()
    assert 'host="0.0.0.0"' in (root / "app.py").read_text()
    assert 'http://localhost:5432' in (root / "app.py").read_text()


def test_rejects_stale_diff_without_writing_any_file(tmp_path):
    root = project(tmp_path)
    plan = analyze(root)
    (root / "app.py").write_text('app.run(host="localhost")\n')
    with pytest.raises(ValueError, match="changed after review"):
        apply_repairs(root, plan)
    assert "node:18" in (root / "Dockerfile").read_text()


def test_missing_input_requires_manual_repair(tmp_path):
    (tmp_path / "Dockerfile").write_text('FROM node:22-alpine\nCOPY dist /app\nCMD ["node", "app.js"]\n')
    report = analyze(tmp_path)
    assert any(f["code"] == "build-inputs" and not f["fixable"] for f in report["findings"])
    assert not report["patches"]


def test_cli_fix_requires_review_and_json_never_prompts(tmp_path):
    root = project(tmp_path)
    runner = CliRunner()
    rejected = runner.invoke(cli, ["doctor", "--path", str(root), "--fix"], input="n\n")
    assert rejected.exit_code == 1
    assert "--- a/Dockerfile" in rejected.output
    assert "node:18" in (root / "Dockerfile").read_text()
    pending = runner.invoke(cli, ["doctor", "--path", str(root), "--fix", "--json"])
    assert json.loads(pending.output)["repairStatus"] == "awaiting_approval"
    accepted = runner.invoke(cli, ["doctor", "--path", str(root), "--fix", "--yes", "--json"])
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.output)["verification"]["build"] == "not_verified"


def test_python_repair_handles_utf8_columns_and_does_not_edit_comments(tmp_path):
    (tmp_path / "Dockerfile").write_text('FROM python:3.11\nCMD ["python", "app.py"]\n')
    (tmp_path / "app.py").write_text('label = "é"; app.run(host="localhost")\n# app.run(host="localhost")\n', encoding="utf-8")
    apply_repairs(tmp_path, analyze(tmp_path))
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == 'label = "é"; app.run(host="0.0.0.0")\n# app.run(host="localhost")\n'


def test_verify_reports_missing_docker_honestly(monkeypatch,tmp_path):
    builder = Mock()
    builder.is_docker_available.return_value = False
    monkeypatch.setattr("ebkit.commands.doctor.DockerBuildValidator", lambda: builder)
    result = verify_project(tmp_path,8080,"/")
    assert result["build"] == result["runtime"] == "not_verified"
    builder.build.assert_not_called()


def test_node_repair_ignores_comments_and_string_examples(tmp_path):
    (tmp_path / "Dockerfile").write_text('FROM node:22-alpine\nCMD ["node", "app.js"]\n')
    before = '// app.listen(8080, "localhost")\n/*\napp.listen(8080, "localhost")\n*/\nconst example = `app.listen(8080, "localhost")`;\napp.listen(8080, "localhost");\n'
    (tmp_path / "app.js").write_text(before)
    result = apply_repairs(tmp_path, analyze(tmp_path))
    assert result["findings"] == []
    assert (tmp_path / "app.js").read_text() == before.rsplit('app.listen(8080, "localhost")', 1)[0] + 'app.listen(8080, "0.0.0.0");\n'


def test_verify_cleans_container_and_image_after_success(monkeypatch,tmp_path):
    builder, runtime, run = Mock(), Mock(), Mock()
    builder.build.return_value.success = True
    runtime.validate.return_value.passed = True
    monkeypatch.setattr("ebkit.commands.doctor.DockerBuildValidator", lambda: builder)
    monkeypatch.setattr("ebkit.commands.doctor.DockerRuntimeValidator", lambda **kwargs: runtime)
    monkeypatch.setattr("ebkit.commands.doctor.subprocess.run", run)
    assert verify_project(tmp_path,8080,"/health")["runtime"] == "passed"
    assert runtime.validate.call_args.kwargs["bind_host"] == "127.0.0.1"
    runtime._stop_container.assert_called_once()
    assert run.call_args.args[0][:3] == ["docker", "image", "rm"]
