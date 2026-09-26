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


class ScheduleConfig(BaseModel):
    """Configuration for CLI run schedule."""
    enabled: bool = False
    frequency: str = "daily"
    time: str = "02:00"
    max_scans: int = 2


class TagConfig(BaseModel):
    """Tier classification thresholds."""
    tier1_min_ratio: float = 0.85
    tier2_min_ratio: float = 0.70


class TierAliasConfig(BaseModel):
    """Provider preference order for one tier's group alias."""
    provider_order: list[str] = []


class TierProvidersConfig(BaseModel):
    """Per-tier provider hierarchy used by ``litellm generate aliases``.

    Each tier names providers (keys of ``[providers.xxx]``) in preference
    order. An empty list means "use the default order" (nvidia first, then
    the remaining configured providers).
    """
    tier1: TierAliasConfig = TierAliasConfig()
    tier2: TierAliasConfig = TierAliasConfig()
    tier3: TierAliasConfig = TierAliasConfig()


class AppConfig(BaseModel):
    """Top-level application config."""

    data_dir: Path = Path()
    verbose: bool = False
    debug: bool = False
    scan_frequency: int = 5
    scan_count: int = 24
    schedule: ScheduleConfig = ScheduleConfig()
    providers: dict[str, ProviderConfig] = {}
    tags: TagConfig = TagConfig()
    tier_providers: TierProvidersConfig = TierProvidersConfig()
    litellm_service_dir: Path = Path("/var/www/local_json_data")
    litellm_config_path: Path = Path("/etc/litellm/litellm.yaml")
    litellm_fallbacks_path: Path = Path("/etc/litellm/litellm-fallbacks.yaml")
    litellm_aliases_path: Path = Path("/etc/litellm/litellm-aliases.yaml")
    litellm_cost_map_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/refs/heads/litellm_internal_staging/model_prices_and_context_window.json"

    def model_post_init(self, __context: object) -> None:
        if self.data_dir == Path():
            self.data_dir = Path(platformdirs.user_data_dir("model-manager"))

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @field_validator("data_dir", "litellm_config_path", "litellm_aliases_path", mode="before")
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

def _format_toml_val(val: object) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, (str, Path)):
        return f'"{val}"'
    elif isinstance(val, list):
        items = [_format_toml_val(item) for item in val]
        return f"[{', '.join(items)}]"
    return f'"{val}"'


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Save the current configuration to a TOML file."""
    target_path = path or find_config()
    if target_path is None:
        target_path = Path(platformdirs.user_config_dir("model-manager")) / "config.toml"

    target_path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    lines: list[str] = []

    # Format top-level non-dict fields
    for key, val in data.items():
        if not isinstance(val, dict):
            lines.append(f"{key} = {_format_toml_val(val)}")

    # Format tables (dicts)
    for key, val in data.items():
        if isinstance(val, dict):
            has_subdicts = any(isinstance(v, dict) for v in val.values())
            if not has_subdicts:
                lines.append("")
                lines.append(f"[{key}]")
                for sub_k, sub_v in val.items():
                    if sub_v is not None:
                        lines.append(f"{sub_k} = {_format_toml_val(sub_v)}")
            else:
                for sub_k, sub_v in val.items():
                    if isinstance(sub_v, dict):
                        lines.append("")
                        lines.append(f"[{key}.{sub_k}]")
                        for k3, v3 in sub_v.items():
                            if v3 is not None:
                                lines.append(f"{k3} = {_format_toml_val(v3)}")
                    else:
                        lines.append("")
                        lines.append(f"[{key}]")
                        lines.append(f"{sub_k} = {_format_toml_val(sub_v)}")

    content = "\n".join(lines).strip() + "\n"
    with open(target_path, "w") as f:
        f.write(content)

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

def get_huggingface_models_path(config: AppConfig) -> Path:
    """Return path to the HuggingFace available models JSON file."""
    return config.data_dir / "huggingface_available_models.json"

def get_litellm_cost_overrides_path(config: AppConfig) -> Path:
    """Return path to the local cost map overrides JSON file."""
    return config.data_dir / "litellm_cost_overrides.json"

def get_litellm_cost_map_output_path(config: AppConfig) -> Path:
    """Return path to the final merged cost map file for LiteLLM service."""
    return config.litellm_service_dir / "model_prices_and_context_window.json"
