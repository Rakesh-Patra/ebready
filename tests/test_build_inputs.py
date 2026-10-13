import json
import pytest
from ebkit.validator.build_inputs import check_build_inputs

def test_missing_assets_and_stage_copy(tmp_path):
    (tmp_path / 'frontend').mkdir()
    with pytest.raises(ValueError, match='missing source'):
        check_build_inputs(tmp_path, 'COPY frontend/dist /app')
    check_build_inputs(tmp_path, 'COPY --from=frontend /app/dist /site')

def test_dependency_and_runtime_conflicts(tmp_path):
    manifest = tmp_path / 'package.json'
    manifest.write_text(json.dumps({'devDependencies': {'vite': '^8.1.0', '@vitejs/plugin-react': '^4.3.1'}}))
    with pytest.raises(ValueError, match='conflicts'):
        check_build_inputs(tmp_path)
    manifest.write_text(json.dumps({'devDependencies': {'vite': '^8.1.0', '@vitejs/plugin-react': '^6.0.2'}}))
    with pytest.raises(ValueError, match='runtime'):
        check_build_inputs(tmp_path, 'FROM node:18-alpine')
    check_build_inputs(tmp_path, 'FROM node:22-alpine\nCOPY . .')
