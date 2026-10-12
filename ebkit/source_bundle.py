"""Bounded ZIP handling. Never execute uploaded project code."""
from __future__ import annotations
import argparse
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 25 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_ENTRIES = 5000
EXCLUDED = {'.git', '.aws', '.ssh', '.insforge', '.docker', '.kube', '.gnupg', 'node_modules', '.venv', 'venv', '__pycache__', '__macosx'}
TEMPLATES = {'.env.example', '.env.sample', '.env.template', 'example.env'}

def included(parts: tuple[str, ...]) -> bool:
    name = parts[-1].lower()
    return not (any(part.lower() in EXCLUDED for part in parts)
                or name == '.ds_store' or (name.startswith('.env') and name not in TEMPLATES)
                or name.endswith(('.pem', '.key', '.p12', '.pfx'))
                or name in {'credentials', 'credentials.json', 'service-account.json', '.npmrc', '.pypirc', '.netrc', '.git-credentials'})

def extract_zip(archive: Path, destination: Path) -> Path:
    archive, destination = Path(archive), Path(destination)
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError('Use a ZIP file no larger than 25 MB.')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('ZIP output directory must be empty.')
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_ENTRIES or sum(item.file_size for item in entries) > MAX_EXPANDED:
            raise ValueError('ZIP exceeds 5,000 entries or 200 MB expanded.')
        selected, seen = [], set()
        for item in entries:
            name = item.orig_filename
            parts = PurePosixPath(name).parts
            mode = item.external_attr >> 16
            reserved = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
            if not parts or '\\' in name or '\0' in name or ':' in name or name.startswith('/') or '..' in parts or any(part.endswith((' ','.')) or reserved.match(part) or any(ord(char)<32 for char in part) for part in parts):
                raise ValueError('ZIP contains an unsafe path.')
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('ZIP links and special files are not supported.')
            if item.flag_bits & 1 or item.file_size > MAX_FILE or item.file_size > max(item.compress_size, 1) * 200:
                raise ValueError('ZIP contains an encrypted, oversized or excessively compressed entry.')
            key = '/'.join(parts).casefold()
            if key in seen:
                raise ValueError('ZIP contains duplicate paths.')
            seen.add(key)
            if not item.is_dir() and included(parts):
                selected.append((item, parts))
        if not selected:
            raise ValueError('ZIP contains no application files.')
        tops = {parts[0] for _, parts in selected}
        strip = len(tops) == 1 and all(len(parts) > 1 for _, parts in selected)
        total = 0
        for item, parts in selected:
            relative = parts[1:] if strip else parts
            target = destination.joinpath(*relative)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('ZIP path escapes the output directory.')
            content = bundle.read(item)
            total += len(content)
            if len(content) != item.file_size or total > MAX_EXPANDED:
                raise ValueError('ZIP expanded size is invalid.')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o700 if (item.external_attr >> 16) & 0o111 else 0o600)
    return destination

def make_zip(root: Path, output: Path) -> Path:
    count = total = 0
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(root.rglob('*')):
            parts = file.relative_to(root).parts
            if file.is_symlink():
                raise ValueError('Source links are not supported.')
            if file.is_file() and included(parts) and file.resolve() != output.resolve():
                count += 1
                total += file.stat().st_size
                if count > MAX_ENTRIES or total > MAX_EXPANDED or file.stat().st_size > MAX_FILE:
                    raise ValueError('Prepared ZIP exceeds the file count or expanded size limits.')
                bundle.write(file, '/'.join(parts))
    if output.stat().st_size > MAX_ARCHIVE:
        output.unlink()
        raise ValueError('Prepared ZIP exceeds 25 MB.')
    return output

def inspect(root: Path) -> dict:
    from ebkit.analyzer.scanner import ProjectScanner
    scan = ProjectScanner(root).scan()
    files = []
    candidates = [file for file in sorted(root.rglob('*')) if file.is_file() and file.stat().st_size <= 65536
                  and included(file.relative_to(root).parts)]
    candidates.sort(key=lambda file: (file.name not in TEMPLATES, file.name not in {'package.json','go.mod','pyproject.toml','requirements.txt','pom.xml','Gemfile'}, str(file)))
    for file in candidates:
        if file.name in TEMPLATES or re.search(r'\.(go|py|js|ts|tsx|jsx|java|rb|php|cs)$', file.name) or file.name in {'package.json','go.mod','pyproject.toml','requirements.txt','pom.xml','Gemfile'}:
            files.append({'path':file.relative_to(root).as_posix(),'text':file.read_text(encoding='utf-8',errors='replace')})
            if len(files) == 40:
                break
    dockerfile = root / 'Dockerfile'
    if dockerfile.exists() and (dockerfile.stat().st_size > 65536 or not dockerfile.is_file()):
        raise ValueError('Root Dockerfile must be a file smaller than 64 KB.')
    return {'scan':scan.as_dict(), 'files':files, 'dockerfile':dockerfile.read_text(encoding='utf-8') if dockerfile.exists() else None, 'port':scan.detected_port or 8080, 'environmentScanComplete':len(candidates) <= 40}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', required=True)
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    try:
        root = extract_zip(Path(options.zip), Path(options.output))
        print(json.dumps(inspect(root)))
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f'{error}\n')

if __name__ == '__main__':
    main()
