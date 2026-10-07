"""
Configuration management for EBKit (~/.ebkit/config).

Stores ONLY non-secret user preferences:
- ai_provider (gemini)

NEVER stores secret keys, tokens, or credentials.
AWS configuration (region, application, environment) is deferred to Day 3 (ebkit deploy).
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Keys that are strictly allowed to be saved in non-secret config
ALLOWED_CONFIG_KEYS = {
    "ai_provider",
}

# Forbidden substrings/patterns in keys (defense-in-depth against secrets)
_FORBIDDEN_KEY_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"key",
        r"secret",
        r"token",
        r"password",
        r"cred",
        r"auth",
        r"private",
    ]
]


@dataclass
class EBKitConfig:
    """Non-secret configuration model for EBKit."""

    ai_provider: str = "gemini"

    def as_dict(self) -> dict[str, str]:
        return {
            "ai_provider": self.ai_provider,
        }

    @property
    def ai_provider_display(self) -> str:
        p = self.ai_provider.lower()
        if p in ("gemini", "google"):
            return "Gemini"
        if p == "openai":
            return "OpenAI (Coming Later)"
        if p == "claude":
            return "Claude (Coming Later)"
        if p in ("groq", "groq api"):
            return "Groq API (Coming Later)"
        return self.ai_provider.capitalize()


def get_default_config_path() -> Path:
    """Return the canonical configuration file path (~/.ebkit/config)."""
    env_path = os.environ.get("EBKIT_CONFIG_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".ebkit" / "config"


def load_config(config_path: Optional[Path] = None) -> Optional[EBKitConfig]:
    """
    Load saved non-secret configuration from disk.

    Returns None if the file does not exist or cannot be parsed.
    """
    path = config_path or get_default_config_path()
    if not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return None

        # Support JSON or INI
        if content.startswith("{"):
            data = json.loads(content)
        else:
            parser = configparser.ConfigParser()
            parser.read_string(content)
            section = "default" if parser.has_section("default") else parser.sections()[0] if parser.sections() else None
            if not section:
                return None
            data = dict(parser.items(section))

        ai_provider = data.get("ai_provider", "gemini")

        return EBKitConfig(
            ai_provider=ai_provider,
        )
    except Exception as exc:
        logger.debug("Failed to read config from %s: %exc", path, exc)
        return None


def save_config(config: EBKitConfig, config_path: Optional[Path] = None) -> None:
    """
    Save non-secret configuration to disk in INI format.

    Enforces that NO sensitive keys or values are stored.
    """
    path = config_path or get_default_config_path()
    data = config.as_dict()

    # Security check: verify no illegal keys
    for k in data.keys():
        if k not in ALLOWED_CONFIG_KEYS:
            raise ValueError(f"Disallowed configuration key: '{k}'")
        for pattern in _FORBIDDEN_KEY_PATTERNS:
            if pattern.search(k):
                raise ValueError(f"Secret-like configuration key forbidden: '{k}'")

    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser["default"] = data

    with path.open("w", encoding="utf-8") as f:
        parser.write(f)
