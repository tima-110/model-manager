"""Domain logic for managing conceptual models."""
from __future__ import annotations

from datetime import datetime
from model_manager.config import AppConfig
from model_manager.domain import storage

def list_models(config: AppConfig) -> list[str]:
    """Return a sorted list of all defined conceptual model IDs."""
    data = storage.load_models_data(config)
    return sorted(data.get("models", {}).keys())

def add_model(
    config: AppConfig,
    model_id: str,
    family: str | None = None,
    display_name: str | None = None,
    default_variant: str | None = None
) -> None:
    """Create or update a conceptual model."""
    data = storage.load_models_data(config)

    if "models" not in data:
        data["models"] = {}

    if model_id not in data["models"]:
        data["models"][model_id] = {
            "display_name": display_name or model_id.replace("-", " ").title(),
            "family": family or "unknown",
            "variants": {},
            "default_variant": "standard"
        }

    model = data["models"][model_id]
    if family:
        model["family"] = family
    if display_name:
        model["display_name"] = display_name
    if default_variant:
        model["default_variant"] = default_variant

    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    storage.save_models_data(config, data)

def resolve_model(model_id: str, config: AppConfig) -> dict | None:
    """Resolve a conceptual model_id to its metadata and variants."""
    data = storage.load_models_data(config)
    model_data = data.get("models", {}).get(model_id)

    if not model_data:
        return None

    return {
        "model": model_id,
        "display_name": model_data.get("display_name"),
        "family": model_data.get("family"),
        "default_variant": model_data.get("default_variant"),
        "variants": [
            {
                "variant_id": vid,
                "aa_slug": vdata.get("aa_slug"),
                "provider_ids": vdata.get("provider_ids", {})
            }
            for vid, vdata in model_data.get("variants", {}).items()
        ]
    }

def remove_model(config: AppConfig, model_id: str) -> bool:
    """Remove a conceptual model from the library. Returns True if removed, False if not found."""
    data = storage.load_models_data(config)
    if model_id in data.get("models", {}):
        del data["models"][model_id]

        if "meta" not in data:
            data["meta"] = {}
        data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        storage.save_models_data(config, data)
        return True
    return False
