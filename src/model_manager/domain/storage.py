"""Shared storage for model and alias data."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from model_manager.config import AppConfig, get_models_path


class StorageError(RuntimeError):
    """Raised when persistent model storage cannot be read safely."""


def _quarantine_corrupt_file(path: Path) -> Path:
    """Move a corrupt file aside under a timestamped `.corrupt-*` name."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    os.replace(path, candidate)
    return candidate


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically using a same-directory temp file, fsync, and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def load_models_data(config: AppConfig) -> dict:
    """Load the models.json file from the user data directory."""
    path = get_models_path(config)
    if not path.exists():
        return {"meta": {}, "models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        quarantined = _quarantine_corrupt_file(path)
        raise StorageError(
            f"Corrupt models file {path} moved to {quarantined}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        quarantined = _quarantine_corrupt_file(path)
        raise StorageError(
            f"Invalid models file {path} (expected JSON object) moved to {quarantined}"
        )
    return data


def save_models_data(config: AppConfig, data: dict) -> None:
    """Save the models.json file to the user data directory atomically."""
    path = get_models_path(config)
    _atomic_write_text(path, json.dumps(data, indent=2))
