"""Domain logic for managing conceptual models."""
from __future__ import annotations

import json
import difflib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from model_manager.config import AppConfig, get_raw_scores_path, get_free_models_path, get_nvidia_models_path, get_ollama_models_path
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

def discover_provider_ids(config: AppConfig, model_id: str, provider: str | None = None) -> List[Dict[str, Any]]:
    """Discover potential provider IDs for a conceptual model using a hybrid AA + string approach."""
    matches = []

    # 1. AA-First Match
    aa_matches = _match_via_aa(config, model_id)
    for m in aa_matches:
        matches.append({
            "provider": m["provider"],
            "provider_id": m["provider_id"],
            "method": "aa",
            "score": 1.0
        })

    # 2. String-Based Match
    string_matches = _match_via_string(config, model_id, provider)
    for m in string_matches:
        # Avoid duplicating AA matches
        if not any(match["provider_id"] == m["provider_id"] for match in matches):
            matches.append({
                "provider": m["provider"],
                "provider_id": m["provider_id"],
                "method": "string",
                "score": m["score"]
            })

    return matches

def _match_via_aa(config: AppConfig, model_id: str) -> List[Dict[str, Any]]:
    """Match via Artificial Analysis slugs with sanity checking."""
    res = resolve_model(model_id, config)
    if not res:
        return []

    aa_slug = None
    for var in res["variants"]:
        if var["variant_id"] == res["default_variant"]:
            aa_slug = var["aa_slug"]
            break

    if not aa_slug:
        return []

    path = get_raw_scores_path(config)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
        models_list = data.get("data", [])
        for m in models_list:
            if m.get("slug") == aa_slug:
                providers = m.get("providers", [])
                if not providers:
                    providers = m.get("deployments", [])

                results = []
                model_keywords = set(model_id.lower().split("-"))

                for p in providers:
                    pid = p.get("id", "").lower()
                    if not pid:
                        continue

                    # Sanity check: provider ID should share at least one significant keyword with model_id
                    # (e.g. "gemma" should be in "openrouter/google/gemma-4-31b-it")
                    pid_keywords = set(pid.replace("/", " ").replace("_", " ").split("-"))
                    if model_keywords & pid_keywords:
                        results.append({"provider": p.get("provider", "unknown"), "provider_id": p.get("id")})

                return results
    except Exception:
        pass

    return []

def _match_via_string(config: AppConfig, model_id: str, provider: str | None = None) -> List[Dict[str, Any]]:
    """Match via string similarity across provider caches, prioritizing substring matches."""
    provider_paths = {
        "openrouter": get_free_models_path(config),
        "nvidia": get_nvidia_models_path(config),
        "ollama": get_ollama_models_path(config),
    }

    matches = []
    model_id_lower = model_id.lower()

    for p_name, path in provider_paths.items():
        if provider and p_name != provider:
            continue

        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text())
            models_list = data.get("models", [])
            for m in models_list:
                mid = m.get("id", "").lower()
                name = m.get("name", "").lower()

                # 1. Substring Match (Highest Confidence)
                if model_id_lower in mid or model_id_lower in name:
                    matches.append({
                        "provider": p_name,
                        "provider_id": m.get("id", ""),
                        "score": 1.0
                    })
                    continue

                # 2. Fuzzy Match on stripped ID
                # Remove common provider prefixes to get a better ratio
                stripped_id = mid.split("/")[-1]
                score_id = difflib.SequenceMatcher(None, model_id_lower, stripped_id).ratio()
                score_name = difflib.SequenceMatcher(None, model_id_lower, name).ratio()
                best_score = max(score_id, score_name)

                if best_score > 0.6:
                    matches.append({
                        "provider": p_name,
                        "provider_id": m.get("id", ""),
                        "score": best_score
                    })
        except Exception:
            continue

    # Sort by score descending
    return sorted(matches, key=lambda x: x["score"], reverse=True)
