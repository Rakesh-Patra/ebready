"""
EBReady Cleanup Service.

Safely cleans temporary EBReady artifacts while strictly protecting user source code.
Ensures .gitignore contains temporary build directories.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Extensions and files that EBReady must NEVER automatically delete
PROTECTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yml",
    ".yaml",
    ".html",
    ".css",
    ".md",
    ".env",
}

PROTECTED_EXACT_FILES = {
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "Pipfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Procfile",
    ".dockerignore",
    ".ebignore",
    ".gitignore",
}

DEFAULT_GITIGNORE_ENTRIES = [
    ".ebready/cache/",
    ".ebready/tmp/",
    ".ebready/build/",
]


class ProjectCleaner:
    """Safely cleans EBReady temporary build files and inspects user artifacts."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()
        self.ebready_dir = self.repo_path / ".ebready"

    def clean_temporary_files(self) -> list[str]:
        """
        Safely remove temporary EBReady directories (.ebready/tmp, .ebready/cache, .ebready/build).
        Returns list of cleaned relative paths.
        """
        cleaned: list[str] = []
        temp_subdirs = ["tmp", "cache", "build"]

        for subdir in temp_subdirs:
            target = self.ebready_dir / subdir
            if target.exists() and target.is_dir():
                try:
                    shutil.rmtree(target, ignore_errors=True)
                    cleaned.append(f".ebready/{subdir}")
                except Exception as exc:
                    logger.warning("Could not remove %s: %s", target, exc)

        # Check for temporary diagnosis or log files directly in .ebready
        if self.ebready_dir.exists():
            for item in self.ebready_dir.glob("temp_*"):
                if item.is_file():
                    try:
                        item.unlink()
                        cleaned.append(f".ebready/{item.name}")
                    except Exception:
                        pass

        return cleaned

    def detect_potentially_unnecessary_files(self) -> list[str]:
        """
        Inspect repository for user files that may be redundant (e.g. old Dockerfiles or backup configs).
        DOES NOT DELETE THEM. Only reports them safely.
        """
        suspicious: list[str] = []
        suspicious_patterns = [
            "old-dockerfile*",
            "*dockerfile.bak*",
            "test-deployment.yml",
            "test-deployment.yaml",
        ]
        for pattern in suspicious_patterns:
            for match in self.repo_path.glob(pattern):
                if match.is_file():
                    rel = str(match.relative_to(self.repo_path))
                    suspicious.append(rel)
        return sorted(suspicious)

    def ensure_gitignore(self) -> bool:
        """
        Ensure .gitignore exists and contains EBReady temporary paths.
        Appends missing entries without modifying existing ones.
        Returns True if new entries were added.
        """
        gitignore_path = self.repo_path / ".gitignore"
        existing_lines: set[str] = set()
        if gitignore_path.exists():
            try:
                content = gitignore_path.read_text(encoding="utf-8", errors="replace")
                existing_lines = {line.strip() for line in content.splitlines()}
            except Exception:
                existing_lines = set()

        missing = [entry for entry in DEFAULT_GITIGNORE_ENTRIES if entry not in existing_lines]
        if missing:
            try:
                prefix = "\n" if gitignore_path.exists() and gitignore_path.stat().st_size > 0 else ""
                with gitignore_path.open("a", encoding="utf-8") as f:
                    f.write(prefix + "# EBReady temporary files\n" + "\n".join(missing) + "\n")
                return True
            except Exception as exc:
                logger.warning("Failed to update .gitignore: %s", exc)
                return False
        return False
