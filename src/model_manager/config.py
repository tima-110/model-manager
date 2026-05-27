"""Configuration models and loader."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, field_validator

class AppConfig(BaseModel):
    """Top-level application config."""

    data_dir: Path = Path()
    verbose: bool = False
    debug: bool = False

    def model_post_init(self, __context: object) -> None:
        if self.data_dir == Path():
            self.data_dir = Path(platformdirs.user_data_dir("model-manager"))

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_home(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

def find_config(override: Path | None = None) -> Path | None:
    if override and override.exists():
        return override
    default = Path(platformdirs.user_config_dir("model-manager")) / "config.toml"
    return default if default.exists() else None

def load_config(path: Path | None = None) -> AppConfig:
    config_path = find_config(path)
    if config_path is None:
        return AppConfig()
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    return AppConfig(**raw)

def get_scores_path(config: AppConfig) -> Path:
    """Return path to the model scores JSON file."""
    return config.data_dir / "model_scores.json"

def get_aliases_path(config: AppConfig) -> Path:
    """Return path to the model aliases JSON file."""
    return config.data_dir / "model_aliases.json"

def get_raw_scores_path(config: AppConfig) -> Path:
    """Return path to the raw AA response JSON file."""
    return config.data_dir / "aa_raw_response.json"

def get_free_models_path(config: AppConfig) -> Path:
    """Return path to the OpenRouter free models JSON file."""
    return config.data_dir / "openrouter_free_models.json"
