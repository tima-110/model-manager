"""Domain logic for managing reference sets."""
from __future__ import annotations

from datetime import datetime
from model_manager.config import AppConfig
from model_manager.domain import storage

def list_reference_sets(config: AppConfig) -> dict:
    """Return all defined reference sets."""
    data = storage.load_models_data(config)
    return data.get("reference_sets", {})

def create_reference_set(config: AppConfig, set_id: str, display_name: str | None = None) -> None:
    """Create a new reference set."""
    data = storage.load_models_data(config)
    if "reference_sets" not in data:
        data["reference_sets"] = {}

    if set_id not in data["reference_sets"]:
        data["reference_sets"][set_id] = {
            "display_name": display_name or set_id.replace("-", " ").title(),
            "items": []
        }

    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    storage.save_models_data(config, data)

def remove_reference_set(config: AppConfig, set_id: str) -> bool:
    """Remove a reference set. Returns True if removed, False if not found."""
    data = storage.load_models_data(config)
    if "reference_sets" in data and set_id in data["reference_sets"]:
        del data["reference_sets"][set_id]

        if "meta" not in data:
            data["meta"] = {}
        data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        storage.save_models_data(config, data)
        return True
    return False

def add_item(config: AppConfig, set_id: str, model: str, variant: str, aa_slug: str) -> bool:
    """Add a model/variant item to a reference set. Returns True if added, False if set not found."""
    data = storage.load_models_data(config)
    if "reference_sets" not in data or set_id not in data["reference_sets"]:
        return False

    items = data["reference_sets"][set_id]["items"]
    # Check if already exists, if so update slug
    found = False
    for item in items:
        if item["model"] == model and item["variant"] == variant:
            item["aa_slug"] = aa_slug
            found = True
            break

    if not found:
        items.append({
            "model": model,
            "variant": variant,
            "aa_slug": aa_slug
        })

    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    storage.save_models_data(config, data)
    return True

def remove_item(config: AppConfig, set_id: str, model: str, variant: str) -> bool:
    """Remove a model/variant item from a reference set. Returns True if removed, False if set or item not found."""
    data = storage.load_models_data(config)
    if "reference_sets" not in data or set_id not in data["reference_sets"]:
        return False

    items = data["reference_sets"][set_id]["items"]
    original_len = len(items)
    data["reference_sets"][set_id]["items"] = [
        item for item in items if not (item["model"] == model and item["variant"] == variant)
    ]

    if len(data["reference_sets"][set_id]["items"]) < original_len:
        if "meta" not in data:
            data["meta"] = {}
        data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        storage.save_models_data(config, data)
        return True

    return False
