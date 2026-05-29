"""Domain logic for managing supported providers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

from model_manager.config import (
    AppConfig,
    get_free_models_path,
    get_nvidia_models_path,
    get_ollama_models_path,
)
from model_manager.domain import auth, discovery

@dataclass
class Provider:
    """Represents a supported model provider and its discovery configuration."""
    name: str
    secret_key: str
    fetch_fn: Callable[..., List[Dict[str, Any]]]
    path_fn: Callable[[AppConfig], Path]
    probe_id: str

# The current list of supported providers.
SUPPORTED_PROVIDERS = [
    Provider(
        name="OpenRouter",
        secret_key="OPENROUTER_API_KEY",
        fetch_fn=discovery.fetch_openrouter_free_models,
        path_fn=get_free_models_path,
        probe_id="openrouter",
    ),
    Provider(
        name="NVIDIA",
        secret_key="NVIDIA_API_KEY",
        fetch_fn=discovery.fetch_nvidia_models,
        path_fn=get_nvidia_models_path,
        probe_id="nvidia",
    ),
    Provider(
        name="Ollama",
        secret_key="OLLAMA_API_KEY",
        fetch_fn=discovery.fetch_ollama_models,
        path_fn=get_ollama_models_path,
        probe_id="ollama",
    ),
]

def list_providers() -> List[Provider]:
    """Return the list of supported providers."""
    return SUPPORTED_PROVIDERS

def run_discovery_workflow(provider: Provider, config: AppConfig, probe: bool = False) -> List[Dict[str, Any]]:
    """
    Orchestrate the discovery process for a provider:
    Fetch -> Optional Probe -> Save.

    Returns the final list of discovered models.
    """
    # 1. Secret Retrieval
    api_key = auth.get_secret(provider.secret_key)

    # OpenRouter public endpoint doesn't strictly require a key,
    # but NVIDIA and Ollama do.
    if provider.name != "OpenRouter" and not api_key:
        raise RuntimeError(f"API key {provider.secret_key} missing from keychain.")

    # 2. Fetch
    # Handle fetch functions that require API key vs those that don't
    if provider.name == "OpenRouter":
        models = provider.fetch_fn()
    else:
        models = provider.fetch_fn(api_key)

    # 3. Optional Probe
    if probe:
        verified_models = []
        for m in models:
            if discovery.probe_model(m["id"], api_key, provider=provider.probe_id):
                verified_models.append(m)
        models = verified_models

    # 4. Save
    path = provider.path_fn(config)
    discovery.save_free_models(config, models, path)

    return models
