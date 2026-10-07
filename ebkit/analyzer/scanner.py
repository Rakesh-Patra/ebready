"""
Repository Scanner — detects project metadata without AI involvement.

Inspects the filesystem for language, framework, dependencies, entry points,
ports, and existing deployment artefacts.  Returns a plain ScanResult
dataclass so that the AI Analyzer can reason about structured facts rather
than raw file contents.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """Structured summary of a scanned repository."""

    # Core language / runtime
    language: Optional[str] = None          # "python", "node", "go", …
    runtime_version: Optional[str] = None  # e.g. "3.12", "20"
    framework: Optional[str] = None         # "fastapi", "express", …

    # Dependency management
    package_manager: Optional[str] = None   # "pip", "npm", "yarn", "poetry"
    dependency_files: list[str] = field(default_factory=list)

    # Entry point / start command
    entrypoint: Optional[str] = None        # "app/main.py", "index.js"
    detected_start_command: Optional[str] = None

    # Network
    detected_port: Optional[int] = None
    port_conflict: bool = False
    port_conflict_details: Optional[str] = None

    # Existing deployment artefacts (True = already present)
    existing_dockerfile: bool = False
    existing_procfile: bool = False
    existing_ebignore: bool = False
    existing_dotenv: bool = False
    existing_ebextensions: bool = False

    # Environment variable files found (names only, no values)
    env_files: list[str] = field(default_factory=list)
    # Environment variable keys detected from files and code (names only, no values)
    detected_env_vars: list[str] = field(default_factory=list)

    # Raw uncertainty notes for the AI
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialisation."""
        return {
            "language": self.language,
            "runtime_version": self.runtime_version,
            "framework": self.framework,
            "package_manager": self.package_manager,
            "dependency_files": self.dependency_files,
            "entrypoint": self.entrypoint,
            "detected_start_command": self.detected_start_command,
            "detected_port": self.detected_port,
            "port_conflict": self.port_conflict,
            "port_conflict_details": self.port_conflict_details,
            "existing_dockerfile": self.existing_dockerfile,
            "existing_procfile": self.existing_procfile,
            "existing_ebignore": self.existing_ebignore,
            "existing_dotenv": self.existing_dotenv,
            "existing_ebextensions": self.existing_ebextensions,
            "env_files": self.env_files,
            "detected_env_vars": self.detected_env_vars,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Port detection helpers
# ---------------------------------------------------------------------------

_PORT_PATTERNS = [
    # EXPOSE in Dockerfile
    re.compile(r"^\s*EXPOSE\s+(\d{2,5})", re.MULTILINE | re.IGNORECASE),
    # uvicorn / gunicorn CLI patterns
    re.compile(r"--port[\s=]+(\d{2,5})"),
    # Node.js / Express listen
    re.compile(r"\.listen\s*\(\s*(\d{2,5})"),
    # PORT env variable default in Python code
    re.compile(r'getenv\s*\(\s*["\']PORT["\']\s*,\s*["\'](\d{2,5})["\']'),
    # process.env.PORT || XXXX
    re.compile(r"process\.env\.PORT\s*\|\|\s*(\d{2,5})"),
    # port=XXXX keyword arg
    re.compile(r"\bport\s*=\s*(\d{2,5})"),
]


def _extract_port(text: str) -> Optional[int]:
    """Return the first port number explicitly found in *text*."""
    for pattern in _PORT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                p = int(m.group(1))
                if 1 <= p <= 65535:
                    return p
            except (ValueError, IndexError):
                pass
    return None


def extract_dockerfile_ports(text: str) -> tuple[list[int], list[int]]:
    """
    Extract exposed ports and command ports from Dockerfile content.
    Returns (exposed_ports, command_ports).
    """
    exposed: list[int] = []
    for m in re.finditer(r"^\s*EXPOSE\s+(\d+)", text, re.MULTILINE | re.IGNORECASE):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535 and p not in exposed:
                exposed.append(p)
        except ValueError:
            pass

    cmd_ports: list[int] = []
    # Match --port 3000, --port=3000, or JSON array format ["--port", "3000"]
    for m in re.finditer(r'--port["\']?[\s=,]+["\']?(\d+)', text, re.IGNORECASE):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535 and p not in cmd_ports:
                cmd_ports.append(p)
        except ValueError:
            pass

    return exposed, cmd_ports


def extract_docker_compose_ports(text: str) -> list[int]:
    """
    Extract container ports from docker-compose.yml / compose.yaml text.
    Matches ports like:
      - "8080:8080"
      - "3000"
      - 5000:5000
      - target: 8080
    """
    ports: list[int] = []
    # Pattern for - "host:container" or - host:container
    for m in re.finditer(r'-\s*["\']?(?:\d+:)?(\d{2,5})["\']?', text):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535 and p not in ports:
                ports.append(p)
        except ValueError:
            pass

    # Pattern for target: 8080
    for m in re.finditer(r'target:\s*(\d{2,5})', text):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535 and p not in ports:
                ports.append(p)
        except ValueError:
            pass

    return ports


# ---------------------------------------------------------------------------
# Framework detection helpers
# ---------------------------------------------------------------------------

_PYTHON_FRAMEWORK_HINTS: dict[str, list[str]] = {
    "fastapi": ["fastapi", "from fastapi", "import fastapi"],
    "flask": ["flask", "from flask", "import flask"],
    "django": ["django", "from django", "import django", "DJANGO_SETTINGS_MODULE"],
    "starlette": ["starlette", "from starlette"],
    "tornado": ["tornado", "from tornado"],
    "aiohttp": ["aiohttp", "from aiohttp"],
    "litestar": ["litestar", "from litestar"],
}

_NODE_FRAMEWORK_HINTS: dict[str, list[str]] = {
    "express": ['"express"', "'express'"],
    "nextjs": ['"next"', "'next'", "next/server"],
    "nuxt": ['"nuxt"', "'nuxt'"],
    "nestjs": ['"@nestjs/core"', "'@nestjs/core'"],
    "fastify": ['"fastify"', "'fastify'"],
    "koa": ['"koa"', "'koa'"],
}


def _detect_python_framework(text: str) -> Optional[str]:
    lower = text.lower()
    for fw, hints in _PYTHON_FRAMEWORK_HINTS.items():
        if any(h in lower for h in hints):
            return fw
    return None


def _detect_node_framework(text: str) -> Optional[str]:
    for fw, hints in _NODE_FRAMEWORK_HINTS.items():
        if any(h in text for h in hints):
            return fw
    return None


# ---------------------------------------------------------------------------
# Start-command builders
# ---------------------------------------------------------------------------

def _build_python_start_command(
    framework: Optional[str],
    entrypoint: Optional[str],
    port: int,
) -> str:
    module = ""
    if entrypoint:
        # Convert path like "app/main.py" → "app.main"
        module = entrypoint.replace("/", ".").replace("\\", ".").removesuffix(".py")

    if framework == "fastapi" or framework == "starlette":
        app_var = "app"
        if module:
            return f"uvicorn {module}:{app_var} --host 0.0.0.0 --port {port}"
        return f"uvicorn main:app --host 0.0.0.0 --port {port}"

    if framework == "flask":
        if module:
            return f"gunicorn {module}:app --bind 0.0.0.0:{port}"
        return f"gunicorn app:app --bind 0.0.0.0:{port}"

    if framework == "django":
        # Best guess; project name unknown
        return f"gunicorn project.wsgi --bind 0.0.0.0:{port}"

    # Fallback
    if entrypoint:
        return f"python {entrypoint}"
    return f"uvicorn main:app --host 0.0.0.0 --port {port}"


def _build_node_start_command(entrypoint: Optional[str]) -> str:
    if entrypoint:
        return f"node {entrypoint}"
    return "node index.js"


# ---------------------------------------------------------------------------
# Main Scanner
# ---------------------------------------------------------------------------


class ProjectScanner:
    """
    Walks a repository and returns a :class:`ScanResult`.

    Only reads files that are cheap and clearly relevant (manifests, entry
    points, Dockerfiles, etc.).  It never reads secrets or binary files.
    """

    # Files that might contain secrets — we note their presence but never read
    _SECRET_FILENAMES = {".env", ".env.local", ".env.production", ".env.staging", ".env.development"}

    # Entry-point candidates per language (checked in order)
    _PYTHON_ENTRYPOINTS = [
        "app/main.py", "main.py", "src/main.py",
        "app/app.py", "app.py", "src/app.py",
        "server.py", "run.py", "wsgi.py", "asgi.py",
        "manage.py",  # Django
    ]
    _NODE_ENTRYPOINTS = [
        "index.js", "src/index.js", "server.js", "src/server.js",
        "app.js", "src/app.js",
    ]

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> ScanResult:
        result = ScanResult()
        self._detect_existing_artefacts(result)
        self._detect_language(result)
        self._detect_env_files(result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_safe(self, rel: str) -> Optional[str]:
        """Read a text file relative to the repo root; return None on error."""
        try:
            return (self.repo_path / rel).read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return None

    def _exists(self, *parts: str) -> bool:
        return (self.repo_path / Path(*parts)).exists()

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    def _detect_language(self, result: ScanResult) -> None:
        if self._detect_python(result):
            return
        if self._detect_node(result):
            return
        result.notes.append("Could not determine project language from known manifest files.")

    def _detect_python(self, result: ScanResult) -> bool:
        dep_files: list[str] = []
        all_dep_text = ""

        for fname in ("requirements.txt", "requirements-dev.txt", "requirements-prod.txt"):
            if self._exists(fname):
                dep_files.append(fname)
                content = self._read_safe(fname) or ""
                all_dep_text += content.lower()

        pyproject = self._read_safe("pyproject.toml")
        if pyproject:
            dep_files.append("pyproject.toml")
            all_dep_text += pyproject.lower()

        setup_py = self._read_safe("setup.py")
        if setup_py:
            dep_files.append("setup.py")
            all_dep_text += setup_py.lower()

        pipfile = self._read_safe("Pipfile")
        if pipfile:
            dep_files.append("Pipfile")
            all_dep_text += pipfile.lower()

        if not dep_files and not self._exists_py_source():
            return False

        result.language = "python"
        result.dependency_files = dep_files

        # Package manager
        if self._exists("Pipfile"):
            result.package_manager = "pipenv"
        elif pyproject and "poetry" in (pyproject or "").lower():
            result.package_manager = "poetry"
        else:
            result.package_manager = "pip"

        # Runtime version — check .python-version or pyproject.toml
        result.runtime_version = self._detect_python_version(pyproject)

        # Framework from dependencies text
        result.framework = _detect_python_framework(all_dep_text)

        # Entry point — search common locations
        entrypoint_text = ""
        for ep in self._PYTHON_ENTRYPOINTS:
            if self._exists(ep):
                result.entrypoint = ep
                entrypoint_text = self._read_safe(ep) or ""
                # Also check framework from source if not found in deps
                if not result.framework:
                    result.framework = _detect_python_framework(entrypoint_text)
                break

        # Procfile takes precedence for start command
        procfile_cmd = self._read_procfile_web_command()
        if procfile_cmd:
            result.detected_start_command = procfile_cmd
            procfile_port = _extract_port(procfile_cmd)
            if procfile_port and not result.detected_port:
                result.detected_port = procfile_port
        else:
            port = result.detected_port or _extract_port(entrypoint_text)
            if port:
                result.detected_port = port
                if not result.detected_start_command:
                    result.detected_start_command = _build_python_start_command(
                        result.framework, result.entrypoint, port
                    )

        # Port — re-check with all available text if not yet detected
        if not result.detected_port:
            all_text = all_dep_text + entrypoint_text
            port = _extract_port(all_text)
            if port:
                result.detected_port = port

        return True

    def _detect_node(self, result: ScanResult) -> bool:
        pkg_json_text = self._read_safe("package.json")
        if not pkg_json_text:
            return False

        result.language = "node"
        result.dependency_files = ["package.json"]

        # Lock file → package manager
        if self._exists("yarn.lock"):
            result.package_manager = "yarn"
        elif self._exists("pnpm-lock.yaml"):
            result.package_manager = "pnpm"
        else:
            result.package_manager = "npm"

        # Runtime version from .nvmrc or .node-version
        result.runtime_version = self._detect_node_version()

        # Framework from package.json
        result.framework = _detect_node_framework(pkg_json_text)

        # Entry point — check package.json "main" field then common names
        import json
        try:
            pkg = json.loads(pkg_json_text)
            main = pkg.get("main") or pkg.get("scripts", {}).get("start", "")
            if main:
                result.entrypoint = main
        except json.JSONDecodeError:
            pass

        for ep in self._NODE_ENTRYPOINTS:
            if self._exists(ep):
                result.entrypoint = result.entrypoint or ep
                break

        entrypoint_text = self._read_safe(result.entrypoint or "") or ""

        # Procfile takes precedence for start command
        procfile_cmd = self._read_procfile_web_command()
        if procfile_cmd:
            result.detected_start_command = procfile_cmd
            procfile_port = _extract_port(procfile_cmd)
            if procfile_port and not result.detected_port:
                result.detected_port = procfile_port

        # Port
        if not result.detected_port:
            port = _extract_port(pkg_json_text + entrypoint_text)
            if port:
                result.detected_port = port

        if not result.detected_start_command:
            result.detected_start_command = _build_node_start_command(result.entrypoint)

        return True

    def _exists_py_source(self) -> bool:
        """True if any .py file exists anywhere in the repo (shallow check)."""
        for p in self.repo_path.rglob("*.py"):
            return True
        return False

    # ------------------------------------------------------------------
    # Version detection
    # ------------------------------------------------------------------

    def _detect_python_version(self, pyproject_text: Optional[str]) -> Optional[str]:
        # .python-version (e.g. used by pyenv)
        pv = self._read_safe(".python-version")
        if pv:
            return pv.strip()

        # pyproject.toml: python = "^3.12"
        if pyproject_text:
            m = re.search(r'python\s*=\s*["\'^~>=!]*(\d+\.\d+)', pyproject_text)
            if m:
                return m.group(1)

        # Dockerfile FROM python:X.Y
        df = self._read_safe("Dockerfile")
        if df:
            m = re.search(r"^FROM\s+python:(\d+\.\d+)", df, re.MULTILINE | re.IGNORECASE)
            if m:
                return m.group(1)

        return None

    def _detect_node_version(self) -> Optional[str]:
        for fname in (".nvmrc", ".node-version"):
            text = self._read_safe(fname)
            if text:
                ver = text.strip().lstrip("v")
                return ver
        return None

    # ------------------------------------------------------------------
    # Existing artefact detection
    # ------------------------------------------------------------------

    def _detect_existing_artefacts(self, result: ScanResult) -> None:
        result.existing_dockerfile = self._exists("Dockerfile") or self._exists("dockerfile")
        result.existing_procfile = self._exists("Procfile")
        result.existing_ebignore = self._exists(".ebignore")
        result.existing_ebextensions = self._exists(".ebextensions")
        result.existing_dotenv = self._exists(".env")

        if result.existing_dockerfile:
            result.notes.append("Existing Dockerfile detected. ebkit will NOT overwrite it without confirmation.")
            df_text = self._read_safe("Dockerfile") or self._read_safe("dockerfile") or ""
            exposed, cmd_ports = extract_dockerfile_ports(df_text)
            if len(exposed) > 1 or (exposed and cmd_ports and set(exposed) != set(cmd_ports)):
                result.port_conflict = True
                conflict_desc = (
                    f"EXPOSE ports {exposed} conflict with command ports {cmd_ports}"
                    if exposed and cmd_ports
                    else f"Multiple conflicting EXPOSE ports: {exposed}"
                )
                result.port_conflict_details = conflict_desc
                result.notes.append(f"Conflicting Dockerfile port declarations: {conflict_desc}")
            elif exposed:
                result.detected_port = exposed[0]
            elif cmd_ports:
                result.detected_port = cmd_ports[0]

            # Detect start command from CMD / ENTRYPOINT if present
            cmd_match = re.search(r'^\s*CMD\s+(?:\[(.*?)\]|(.*))$', df_text, re.MULTILINE)
            if cmd_match:
                if cmd_match.group(1):
                    import json
                    try:
                        cmd_items = json.loads(f"[{cmd_match.group(1)}]")
                        result.detected_start_command = " ".join(cmd_items)
                    except Exception:
                        pass
                elif cmd_match.group(2):
                    result.detected_start_command = cmd_match.group(2).strip()

        if result.existing_procfile:
            result.notes.append("Existing Procfile detected. ebkit will NOT overwrite it without confirmation.")
        if result.existing_ebextensions:
            result.notes.append("Existing .ebextensions/ detected. ebkit will NOT overwrite it without confirmation.")

        # Check Docker Compose files if port not already detected
        if not result.detected_port:
            for compose_file in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                if self._exists(compose_file):
                    compose_text = self._read_safe(compose_file) or ""
                    compose_ports = extract_docker_compose_ports(compose_text)
                    if compose_ports:
                        result.detected_port = compose_ports[0]
                        result.notes.append(f"Application port {compose_ports[0]} detected from {compose_file}.")
                        break

    # ------------------------------------------------------------------
    # Environment file detection (names only, no values)
    # ------------------------------------------------------------------

    def _detect_env_files(self, result: ScanResult) -> None:
        env_patterns = [".env*", "*.env"]
        env_found: list[str] = []
        keys_found: set[str] = set()

        for pattern in env_patterns:
            for p in self.repo_path.glob(pattern):
                if p.is_file():
                    env_found.append(p.name)
                    # Safely read variable keys ONLY — never values
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        for line in content.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key = line.split("=", 1)[0].strip()
                                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                                    keys_found.add(key)
                    except Exception:
                        pass

        # Also detect environment variables referenced in application source code
        code_keys = self._detect_code_env_vars()
        keys_found.update(code_keys)

        result.env_files = sorted(set(env_found))
        result.detected_env_vars = sorted(keys_found)
        if result.env_files:
            result.notes.append(
                f"Environment files detected ({', '.join(result.env_files)}). "
                "Values will NOT be copied into generated .env.example."
            )

    def _detect_code_env_vars(self) -> set[str]:
        keys = set()
        py_patterns = [
            re.compile(r'os\.environ\.get\(\s*["\']([A-Za-z0-9_]+)["\']'),
            re.compile(r'os\.getenv\(\s*["\']([A-Za-z0-9_]+)["\']'),
            re.compile(r'os\.environ\[\s*["\']([A-Za-z0-9_]+)["\']'),
        ]
        js_patterns = [
            re.compile(r'process\.env\.([A-Za-z0-9_]+)'),
            re.compile(r'process\.env\[\s*["\']([A-Za-z0-9_]+)["\']'),
        ]
        ignore_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
        for p in self.repo_path.rglob("*"):
            if not p.is_file():
                continue
            if any(part in ignore_dirs for part in p.parts):
                continue
            if p.suffix in (".py",):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    for pat in py_patterns:
                        for m in pat.finditer(text):
                            keys.add(m.group(1))
                except Exception:
                    pass
            elif p.suffix in (".js", ".ts", ".mjs"):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    for pat in js_patterns:
                        for m in pat.finditer(text):
                            keys.add(m.group(1))
                except Exception:
                    pass
        return keys

    # ------------------------------------------------------------------
    # Procfile helper
    # ------------------------------------------------------------------

    def _read_procfile_web_command(self) -> Optional[str]:
        text = self._read_safe("Procfile")
        if not text:
            return None
        for line in text.splitlines():
            if line.startswith("web:"):
                return line[4:].strip()
        return None
