"""Exercise the staged index: ignored files can still be force-added."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/check_staged_secrets.py'


@pytest.mark.parametrize('name,blocked', [
    ('.env', True), ('config/private key.pem', True),
    ('infra/deployment.tfvars.json', True), ('infra/terraform.tfstate.backup', True),
    ('web/.env.example', False), ('infra/terraform.tfvars.example', False),
])
def test_forced_staged_private_paths(tmp_path, name, blocked):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, env=env, check=True)
    file = tmp_path / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('PRIVATE_CONTENT_MUST_NOT_BE_PRINTED', encoding='utf-8')
    subprocess.run(['git', 'add', '-f', '--', name], cwd=tmp_path, env=env, check=True)
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == int(blocked)
    assert 'PRIVATE_CONTENT_MUST_NOT_BE_PRINTED' not in result.stdout + result.stderr


def test_unstaged_private_file_does_not_block_safe_commit(tmp_path):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, env=env, check=True)
    (tmp_path / '.env').write_text('PRIVATE_CONTENT_MUST_NOT_BE_PRINTED', encoding='utf-8')
    (tmp_path / 'app.py').write_text('print("hello")', encoding='utf-8')
    subprocess.run(['git', 'add', 'app.py'], cwd=tmp_path, env=env, check=True)
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0
