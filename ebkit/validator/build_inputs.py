"""Static build checks only; never execute project code or build an image."""
import json
import re
import shlex
from pathlib import Path


def check_build_inputs(root: Path, dockerfile: str = "") -> None:
    check_npm_lock_inputs(root, dockerfile)
    manifests = (file for file in root.rglob("package.json") if not {"node_modules", ".git"}.intersection(file.parts))
    for file in manifests:
        try:
            manifest = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid manifest: {file.relative_to(root)}") from exc
        deps = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
        def major(value):
            match = re.match(r"[~^]?(\d+)", str(value or ""))
            return int(match[1]) if match else 0
        vite, plugin = major(deps.get("vite")), major(deps.get("@vitejs/plugin-react"))
        if vite >= 8 and 0 < plugin < 5:
            raise ValueError(f"{file.relative_to(root)}: Vite {vite} conflicts with React plugin {plugin}. Update compatible dependencies and regenerate the lockfile.")
        if vite >= 7 and re.search(r"^\s*FROM\s+node:(?:1\d|20\.(?:\d|1[0-8]))(?:[.\s-]|$)", dockerfile, re.M | re.I):
            raise ValueError("Dockerfile Node runtime is incompatible with Vite. Use Node 22.12+ or a supported newer version.")
    for line in dockerfile.replace("\\\n", " ").splitlines():
        match = re.match(r"\s*(?:COPY|ADD)\s+(.+)", line, re.I)
        if not match or "--from" in match[1]:
            continue
        text = re.sub(r"--\S+\s*", "", match[1])
        try:
            sources = (json.loads(text) if text.lstrip().startswith("[") else shlex.split(text))[:-1]
        except ValueError:
            continue
        if any("$" in source or source.startswith(("http://", "https://")) for source in sources):
            continue
        def exists(source):
            source = source.lstrip("/")
            return bool(list(root.glob(source))) if any(c in source for c in "*?") else (root / source).exists()
        missing = [source for source in sources if not any(c in source for c in "*?") and not exists(source)]
        if missing or (sources and not any(exists(source) for source in sources)):
            raise ValueError(f"Dockerfile copies missing source: {', '.join(missing or sources)}. Build generated assets inside Docker and COPY --from their build stage.")


def check_npm_lock_inputs(root: Path, dockerfile: str) -> None:
    """Reject npm ci when the lockfile is absent or was never copied into its stage."""
    packages = {}
    for line in dockerfile.replace('\\\n',' ').splitlines():
        if re.match(r'\s*FROM\s+',line,re.I):
            packages = {}
        match = re.match(r'\s*COPY\s+(.+)',line,re.I)
        if match and '--from' not in match[1]:
            text = re.sub(r'--\S+\s*','',match[1])
            try:
                sources = (json.loads(text) if text.lstrip().startswith('[') else shlex.split(text))[:-1]
            except ValueError:
                continue
            for source in sources:
                if '$' in source:
                    continue
                paths = list(root.glob(source)) if any(char in source for char in '*?') else [root/source]
                for path in paths:
                    candidates = [path/'package.json',path/'package-lock.json',path/'npm-shrinkwrap.json'] if path.is_dir() else [path]
                    for candidate in candidates:
                        if candidate.is_file() and candidate.name in {'package.json','package-lock.json','npm-shrinkwrap.json'}:
                            packages.setdefault(candidate.parent,set()).add(candidate.name)
        if re.match(r'\s*RUN\s+',line,re.I) and re.search(r'\bnpm(?:\s+--\S+)*\s+ci\b',line):
            if not packages or any('package.json' in names and not {'package-lock.json','npm-shrinkwrap.json'}.intersection(names) for names in packages.values()):
                raise ValueError('npm ci requires an existing package-lock.json or npm-shrinkwrap.json copied into its build stage before installation. Generate/review a lockfile; dependency-free projects can skip npm installation.')
