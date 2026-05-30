"""Logic for model aliasing, resolution, and discovery."""
from __future__ import annotations

import json
import difflib
from datetime import datetime
from pathlib import Path

from model_manager.config import AppConfig, get_scores_path
from model_manager.domain import storage, models

def resolve_id(provider_id: str, config: AppConfig) -> dict | None:
    """
    Resolves a provider_id to its AA slug and retrieves scores.
    """
    data = storage.load_models_data(config)
    scores = load_json_safe(get_scores_path(config))

    # 1. Explicit Match
    for model_id, model_data in data.get("models", {}).items():
        for variant_id, variant_data in model_data.get("variants", {}).items():
            for provider, ids in variant_data.get("provider_ids", {}).items():
                # Handle both old list and new dict structure
                if isinstance(ids, list):
                    found = provider_id in ids
                else:
                    found = provider_id in ids

                if found:
                    aa_slug = variant_data.get("aa_slug")
                    score_data = scores.get("models", {}).get(aa_slug)
                    return {
                        "model": model_id,
                        "variant": variant_id,
                        "aa_slug": aa_slug,
                        "scores": score_data,
                        "method": "explicit"
                    }

    # 2. Model Key Match (Fallback to default variant)
    if provider_id in data.get("models", {}):
        model_data = data["models"][provider_id]
        default_variant = model_data.get("default_variant")
        if default_variant:
            variant_data = model_data["variants"].get(default_variant)
            if variant_data:
                aa_slug = variant_data.get("aa_slug")
                score_data = scores.get("models", {}).get(aa_slug)
                return {
                    "model": provider_id,
                    "variant": default_variant,
                    "aa_slug": aa_slug,
                    "scores": score_data,
                    "method": "default_fallback"
                }

    return None

def load_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

def add_alias(config: AppConfig, model_id: str, provider: str | None = None, provider_id: str | None = None, variant_id: str = "standard", family: str | None = None, display_name: str | None = None, aa_slug: str | None = None, scores: dict | None = None) -> None:
    """Registers a provider_id to a model variant, or creates a skeleton model."""
    # Ensure the conceptual model exists first
    models.add_model(config, model_id, family=family, display_name=display_name)


    data = storage.load_models_data(config)
    model = data["models"][model_id]
    if variant_id not in model["variants"]:
        model["variants"][variant_id] = {
            "aa_slug": None,
            "provider_ids": {},
            "notes": ""
        }

    variant = model["variants"][variant_id]

    if provider and provider_id:
        # Ensure 1-to-1 mapping from ID to Variant
        for mid, mdata in data["models"].items():
            for vid, vdata in mdata["variants"].items():
                for prov, ids in vdata["provider_ids"].items():
                    if provider_id in ids:
                        if isinstance(ids, list):
                            ids.remove(provider_id)
                        else:
                            del ids[provider_id]

        if provider not in variant["provider_ids"]:
            variant["provider_ids"][provider] = {}

        # Convert list to dict if this is an old mapping
        if isinstance(variant["provider_ids"][provider], list):
            old_ids = variant["provider_ids"][provider]
            variant["provider_ids"][provider] = {pid: {} for pid in old_ids}

        if provider_id not in variant["provider_ids"][provider]:
            variant["provider_ids"][provider][provider_id] = {}

    if aa_slug:
        variant["aa_slug"] = aa_slug
    if scores:
        variant["scores"] = scores


    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    storage.save_models_data(config, data)

def discover_aliases(config: AppConfig, provider: str, ids: list[str]) -> list[dict]:
    """Suggests AA slugs for unmapped provider IDs."""
    data = storage.load_models_data(config)
    scores = load_json_safe(get_scores_path(config))
    all_aa_slugs = list(scores.get("models", {}).keys())
    all_aa_names = [m.get("name") for m in scores.get("models", {}).values() if m.get("name")]

    suggestions = []
    for pid in ids:
        if resolve_id(pid, config):
            continue

        clean_id = pid.split("/")[-1].split(":")[0].lower()
        slug_matches = difflib.get_close_matches(clean_id, all_aa_slugs, n=3, cutoff=0.4)
        name_matches = difflib.get_close_matches(clean_id, all_aa_names, n=3, cutoff=0.4)
        candidates = slug_matches + [s for s in name_matches if s not in slug_matches]

        if candidates:
            suggestions.append({
                "pid": pid,
                "suggestions": candidates[:3]
            })

    return suggestions

def audit_mappings(config: AppConfig, known_ids: list[str]) -> dict:
    """Identifies which known IDs are missing mappings."""
    mapped = []
    missing = []

    for pid in known_ids:
        if resolve_id(pid, config):
            mapped.append(pid)
        else:
            missing.append(pid)

    return {
        "total": len(known_ids),
        "mapped": len(mapped),
        "missing": len(missing),
        "missing_ids": missing
    }
