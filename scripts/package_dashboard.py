"""Create an Elastic Beanstalk source bundle without credentials or local state."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def package(output: Path) -> Path:
    files = [ROOT / name for name in ('Dockerfile', '.dockerignore', 'pyproject.toml', 'README.md')]
    files += [ROOT / 'web' / name for name in ('package.json', 'package-lock.json', 'index.html', 'tsconfig.json', 'vite.config.ts')]
    for directory, suffixes in [('ebkit', {'.py', '.j2'}), ('web/src', {'.ts', '.tsx', '.css', '.svg', '.png', '.jpg', '.woff2'}), ('web/server', {'.mjs'})]:
        files.extend(p for p in (ROOT / directory).rglob('*')
                     if p.is_file() and p.suffix in suffixes
                     and not any(part.startswith('.') or part in {'node_modules', '__pycache__'} for part in p.relative_to(ROOT).parts)
                     and '.test.' not in p.name)
    if (ROOT / 'web/public').exists():
        raise SystemExit('Review web/public assets before adding them to the bundle allowlist.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            archive.write(file, file.relative_to(ROOT).as_posix())
    return output


if __name__ == '__main__':
    print(package(ROOT / 'artifacts' / 'ebkit-dashboard.zip'))
