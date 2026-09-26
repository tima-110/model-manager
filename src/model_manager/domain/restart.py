"""Domain logic for handling LiteLLM restart requests."""
from __future__ import annotations

import getpass
import json
from datetime import datetime, timezone
from pathlib import Path

from model_manager.config import AppConfig


def request_restart(config: AppConfig, reason: str | None = None) -> dict[str, str]:
    """Record a restart request by appending a JSON object line to the restart request file.

    Args:
        config: Application configuration containing the restart request file path.
        reason: Optional description of why the restart was requested.

    Returns:
        dict: The recorded request payload.
    """
    target_path = config.litellm_restart_request_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, str] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason or "CLI restart request",
    }

    try:
        record["requested_by"] = getpass.getuser()
    except Exception:
        pass

    with open(target_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return record
