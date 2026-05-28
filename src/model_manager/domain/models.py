"""Domain logic for managing conceptual models."""
from __future__ import annotations

import json
import difflib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

def search_aa_candidates(config: AppConfig, query: str) -> List[Dict[str, str]]:
    """Search the raw AA dataset for potential model candidates based on a query."""
    path = get_raw_scores_path(config)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
        models_list = data.get("data", [])
        query_lower = query.lower()
        candidates = []

        for m in models_list:
            slug = m.get("slug", "").lower()
            name = m.get("name", "").lower()

            # Substring match (Highest confidence)
            if query_lower in slug or query_lower in name:
                candidates.append({"slug": m.get("slug"), "name": m.get("name")})
                continue

            # Fuzzy match (Slightly lower confidence)
            slug_ratio = difflib.SequenceMatcher(None, query_lower, slug).ratio()
            name_ratio = difflib.SequenceMatcher(None, query_lower, name).ratio()
            if max(slug_ratio, name_ratio) > 0.6:
                candidates.append({"slug": m.get("slug"), "name": m.get("name")})

        # Remove duplicates and sort
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["slug"] and c["slug"] not in seen:
                unique_candidates.append(c)
                seen.add(c["slug"])

        return unique_candidates
    except Exception:
        return []

def discover_provider_ids(config: AppConfig, model_id: str, variant_slugs: List[Tuple[str, str]], provider: str | None = None) -> List[Dict[str, Any]]:
    """Discover potential provider IDs for multiple model variants using AA + string approach."""
    matches = []

    # 1. AA-First Match for all variants
    aa_matches = _match_via_aa_multi(config, variant_slugs)
    for m in aa_matches:
        matches.append({
            "provider": m["provider"],
            "provider_id": m["provider_id"],
            "method": "aa",
            "score": 1.0,
            "variant_id": m["variant_id"],
            "aa_name": m.get("aa_name")
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
                "score": m["score"],
                "variant_id": "standard" # Default for string matches
            })

    return matches

def _match_via_aa_multi(config: AppConfig, variant_slugs: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Match multiple slugs to providers in the AA dataset."""
    path = get_raw_scores_path(config)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
        models_list = data.get("data", [])
        results = []

        for var_id, slug in variant_slugs:
            if not slug:
                continue

            # Find the AA model entry for this slug
            for m in models_list:
                if m.get("slug") == slug:
                    providers = m.get("providers", []) or m.get("deployments", [])
                    # Use a representative model_id for sanity checking (approximate from slug)
                    model_keywords = set(slug.lower().split("-"))

                    for p in providers:
                        pid = p.get("id", "").lower()
                        if not pid:
                            continue

                        pid_keywords = set(pid.replace("/", " ").replace("_", " ").split("-"))
                        if model_keywords & pid_keywords:
                            results.append({
                                "provider": p.get("provider", "unknown"),
                                "provider_id": p.get("id"),
                                "variant_id": var_id,
                                "aa_name": m.get("name", "unknown")
                            })
                    break # Found the model for this slug
        return results
    except Exception:
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
