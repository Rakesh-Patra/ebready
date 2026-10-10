"""Local, non-secret metadata for deployments managed by EBKit."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_state_path() -> Path:
    configured_path = os.environ.get("EBKIT_STATE_FILE")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".ebkit" / "deployments.json"


def load_deployments(path: Path | None = None) -> list[dict[str, Any]]:
    state_path = path or get_state_path()
    if not state_path.exists():
        return []
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read EBKit deployment state at '{state_path}': {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("deployments"), list):
        raise ValueError(f"EBKit deployment state at '{state_path}' has an invalid format.")
    return [item for item in data["deployments"] if isinstance(item, dict)]


def save_deployment(
    deployment: dict[str, Any],
    path: Path | None = None,
) -> None:
    state_path = path or get_state_path()
    deployments = load_deployments(state_path)
    identity = (
        deployment.get("account_id"),
        deployment.get("region"),
        deployment.get("environment_name"),
    )
    deployments = [
        item
        for item in deployments
        if (
            item.get("account_id"),
            item.get("region"),
            item.get("environment_name"),
        ) != identity
    ]
    record = dict(deployment)
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    deployments.append(record)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as state_file:
            temp_path = Path(state_file.name)
            json.dump({"version": 1, "deployments": deployments}, state_file, indent=2)
            state_file.write("\n")
        try:
            temp_path.replace(state_path)
        except OSError:
            if os.name != "nt":
                os.chmod(temp_path, 0o600)
            raise
        if os.name != "nt":
            os.chmod(state_path, 0o600)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def remove_deployment(
    account_id: str,
    region: str,
    environment_name: str,
    path: Path | None = None,
) -> None:
    state_path = path or get_state_path()
    deployments = load_deployments(state_path)
    remaining = [
        item
        for item in deployments
        if not (
            item.get("account_id") == account_id
            and item.get("region") == region
            and item.get("environment_name") == environment_name
        )
    ]
    if len(remaining) == len(deployments):
        return

    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as state_file:
            temp_path = Path(state_file.name)
            json.dump({"version": 1, "deployments": remaining}, state_file, indent=2)
            state_file.write("\n")
        temp_path.replace(state_path)
        if os.name != "nt":
            os.chmod(state_path, 0o600)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
