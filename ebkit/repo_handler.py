"""
Safe GitHub repository handling and validation.

Ensures:
- GitHub URL is validated and strictly checked
- Unsupported URLs and injection attacks are rejected
- Git clone is executed safely without executing repository code
- Temporary directories are cleaned up when processing completes
- Credentials/tokens are never exposed or printed
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Pattern for valid GitHub repository path: /owner/repo[.git]
_GITHUB_PATH_RE = re.compile(r"^/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+(?:\.git)?/?$")

# Disallowed characters in repository URL to prevent shell/CLI injection
_UNSAFE_CHARS_RE = re.compile(r"[\s`$|&;<>\\]")


def validate_github_url(url: str) -> tuple[bool, str]:
    """
    Validate that *url* is a legitimate, safe GitHub repository URL.

    Returns (is_valid: bool, error_message: str).
    """
    if not url or not isinstance(url, str):
        return False, "Repository URL cannot be empty."

    trimmed = url.strip()
    if not trimmed:
        return False, "Repository URL cannot be empty."

    # Prevent option injection (e.g. `--upload-pack=...`)
    if trimmed.startswith("-"):
        return False, "Repository URL cannot start with a hyphen or CLI flag."

    # Prevent shell injection characters
    if _UNSAFE_CHARS_RE.search(trimmed):
        return False, "Repository URL contains invalid or unsafe characters."

    # Prevent path traversal
    if ".." in trimmed:
        return False, "Repository URL contains path traversal sequences."

    try:
        parsed = urlparse(trimmed)
    except Exception as exc:
        return False, f"Failed to parse URL: {exc}"

    # Only http and https schemes are permitted
    if parsed.scheme.lower() not in ("https", "http"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only https:// GitHub URLs are supported."

    # Check hostname
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("github.com", "www.github.com"):
        return False, f"Unsupported host '{hostname}'. Only GitHub repositories (github.com) are supported."

    # Credentials must not be embedded in URL
    if parsed.username or parsed.password:
        return False, "Repository URL must not contain embedded credentials."

    # Validate repository path format: /owner/repo
    path = parsed.path
    if not _GITHUB_PATH_RE.match(path):
        return False, f"Invalid GitHub repository path '{path}'. Expected format: https://github.com/owner/repository"

    return True, ""


def mask_credentials(text: str) -> str:
    """Mask any potential tokens, passwords, or credentials in text."""
    # Mask embedded credentials in URLs (e.g. https://token@github.com)
    text = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://***:***@", text)
    text = re.sub(r"://([^@\s]+)@", r"://***@", text)
    # Mask common token formats (e.g. ghp_..., gho_...)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9_]{36,}", "***GITHUB_TOKEN***", text)
    return text


def safe_clone_repo(
    url: str,
    dest_dir: Optional[Path] = None,
    timeout: int = 120,
) -> Path:
    """
    Safely clone a GitHub repository into a temporary directory.

    Guarantees:
    - URL is strictly validated
    - git clone executed with `--` delimiter against flag injection
    - GIT_TERMINAL_PROMPT=0 prevents interactive hanging
    - Repository code is NOT executed
    - Credentials are never logged or exposed

    Raises:
        ValueError: if the URL fails validation
        RuntimeError: if Git is unavailable or clone fails
    """
    valid, err_msg = validate_github_url(url)
    if not valid:
        raise ValueError(err_msg)

    if not shutil.which("git"):
        raise RuntimeError("'git' is not installed or available in PATH.")

    target_dir = dest_dir or Path(tempfile.mkdtemp(prefix="ebready-repo-"))

    clean_env = os.environ.copy()
    clean_env["GIT_TERMINAL_PROMPT"] = "0"

    cmd = ["git", "clone", "--depth", "1", "--", url.strip(), str(target_dir)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_env,
        )
        if proc.returncode != 0:
            err = mask_credentials(proc.stderr or proc.stdout)
            if dest_dir is None and target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(f"git clone failed: {err.strip()}")
    except subprocess.TimeoutExpired:
        if dest_dir is None and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        raise RuntimeError(f"git clone timed out after {timeout} seconds.")
    except Exception as exc:
        if dest_dir is None and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError(f"git clone error: {exc}")

    return target_dir
