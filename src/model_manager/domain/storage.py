"""Shared storage for model and alias data."""
from __future__ import annotations

import json
from pathlib import Path
from model_manager.config import AppConfig, get_models_path

def load_models_data(config: AppConfig) -> dict:
    """Load the models.json file from the user data directory."""
    path = get_models_path(config)
    if not path.exists():
        return {"meta": {}, "models": {}}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"meta": {}, "models": {}}

def save_models_data(config: AppConfig, data: dict) -> None:
    """Save the models.json file to the user data directory."""
    path = get_models_path(config)
    path.write_text(json.dumps(data, indent=2))
