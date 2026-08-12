"""Configuration models and loader."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, field_validator

class ProviderScanConfig(BaseModel):
    """Per-provider overrides for scan behavior."""
    scan_concurrency: int | None = None
    scan_delay_between_models_ms: int | None = None
    cycle_delay_sec: int | None = None


class ProviderOutputConfig(BaseModel):
    """Per-provider config for YAML generation output."""
    keys: list[str] = []
    output_path: Path | None = None
    litellm_prefix: str = ""
    rpm: int | None = None
    api_base: str | None = None


class ProviderConfig(ProviderScanConfig, ProviderOutputConfig):
    """Combined per-provider config from [providers.xxx] TOML sections.

    Contains both scan-behavior overrides and YAML output configuration.
    """


class TagConfig(BaseModel):
    """Tier classification thresholds."""
    tier1_min_ratio: float = 0.85
    tier2_min_ratio: float = 0.70


class AppConfig(BaseModel):
    """Top-level application config."""

    data_dir: Path = Path()
    verbose: bool = False
    debug: bool = False
    scan_frequency: int = 5
    scan_count: int = 24
    providers: dict[str, ProviderConfig] = {}
    tags: TagConfig = TagConfig()
    litellm_service_dir: Path = Path("/var/www/local_json_data")
    litellm_config_path: Path = Path("/etc/litellm/litellm.yaml")
    litellm_cost_map_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/refs/heads/litellm_internal_staging/model_prices_and_context_window.json"

    def model_post_init(self, __context: object) -> None:
        if self.data_dir == Path():
            self.data_dir = Path(platformdirs.user_data_dir("model-manager"))

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @field_validator("data_dir", "litellm_config_path", mode="before")
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

def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Save the current configuration to a TOML file."""
    target_path = path or find_config()
    if target_path is None:
        # This should ideally not happen as find_config has a default
        raise RuntimeError("Could not determine configuration path")

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate simple TOML content from the config model
    lines = []
    for key, value in config.model_dump().items():
        if isinstance(value, Path):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f'{key} = {value}')

    with open(target_path, "w") as f:
        f.write("\n".join(lines))

    return target_path

def get_scores_path(config: AppConfig) -> Path:
    """Return path to the model scores JSON file."""
    return config.data_dir / "model_scores.json"

def get_models_path(config: AppConfig) -> Path:
    """Return path to the models JSON file."""
    return config.data_dir / "models.json"

def get_raw_scores_path(config: AppConfig) -> Path:
    """Return path to the raw AA response JSON file."""
    return config.data_dir / "aa_raw_response.json"

def get_free_models_path(config: AppConfig) -> Path:
    """Return path to the OpenRouter free models JSON file."""
    return config.data_dir / "openrouter_free_models.json"

def get_nvidia_models_path(config: AppConfig) -> Path:
    """Return path to the NVIDIA available models JSON file."""
    return config.data_dir / "nvidia_available_models.json"

def get_ollama_models_path(config: AppConfig) -> Path:
    """Return path to the Ollama available models JSON file."""
    return config.data_dir / "ollama_available_models.json"

def get_gemini_models_path(config: AppConfig) -> Path:
    """Return path to the Gemini available models JSON file."""
    return config.data_dir / "gemini_available_models.json"

def get_litellm_cost_overrides_path(config: AppConfig) -> Path:
    """Return path to the local cost map overrides JSON file."""
    return config.data_dir / "litellm_cost_overrides.json"

def get_litellm_cost_map_output_path(config: AppConfig) -> Path:
    """Return path to the final merged cost map file for LiteLLM service."""
    return config.litellm_service_dir / "model_prices_and_context_window.json"
