"""Logic for model aliasing, resolution, and discovery."""
from __future__ import annotations

import json
import difflib
from datetime import datetime
from pathlib import Path

from model_manager.config import AppConfig, get_aliases_path, get_scores_path

def load_aliases(config: AppConfig) -> dict:
    path = get_aliases_path(config)
    if not path.exists():
        return {"meta": {}, "models": {}}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"meta": {}, "models": {}}

def save_aliases(config: AppConfig, aliases: dict) -> None:
    path = get_aliases_path(config)
    path.write_text(json.dumps(aliases, indent=2))

def resolve_id(provider_id: str, config: AppConfig) -> dict | None:
    """
    Resolves a provider_id to its AA slug and retrieves scores.
    """
    aliases = load_aliases(config)
    scores = load_json_safe(get_scores_path(config))

    # 1. Explicit Match
    for model_id, model_data in aliases.get("models", {}).items():
        for variant_id, variant_data in model_data.get("variants", {}).items():
            for provider, ids in variant_data.get("provider_ids", {}).items():
                if provider_id in ids:
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
    if provider_id in aliases.get("models", {}):
        model_data = aliases["models"][provider_id]
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

def resolve_model(model_id: str, config: AppConfig) -> dict | None:
    """
    Resolves a conceptual model_id to its variants and providers.
    """
    aliases = load_aliases(config)
    model_data = aliases.get("models", {}).get(model_id)

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

def load_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

def add_alias(config: AppConfig, provider: str | None = None, provider_id: str | None = None, model_id: str, variant_id: str = "standard", family: str | None = None, display_name: str | None = None, aa_slug: str | None = None) -> None:
    """Registers a provider_id to a model variant, or creates a skeleton model."""
    aliases = load_aliases(config)

    if "models" not in aliases:
        aliases["models"] = {}

    if model_id not in aliases["models"]:
        aliases["models"][model_id] = {
            "display_name": display_name or model_id.replace("-", " ").title(),
            "family": family or "unknown",
            "variants": {},
            "default_variant": "standard"
        }

    if provider is None or provider_id is None:
        save_aliases(config, aliases)
        return

    model = aliases["models"][model_id]
    if variant_id not in model["variants"]:
        model["variants"][variant_id] = {
            "aa_slug": None,
            "provider_ids": {},
            "notes": ""
        }

    variant = model["variants"][variant_id]

    # Ensure 1-to-1 mapping from ID to Variant
    for mid, mdata in aliases["models"].items():
        for vid, vdata in mdata["variants"].items():
            for prov, ids in vdata["provider_ids"].items():
                if provider_id in ids:
                    ids.remove(provider_id)

    if provider not in variant["provider_ids"]:
        variant["provider_ids"][provider] = []
    if provider_id not in variant["provider_ids"][provider]:
        variant["provider_ids"][provider].append(provider_id)

    if aa_slug:
        variant["aa_slug"] = aa_slug

    if "meta" not in aliases:
        aliases["meta"] = {}
    aliases["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    save_aliases(config, aliases)

def discover_aliases(config: AppConfig, provider: str, ids: list[str]) -> list[dict]:
    """Suggests AA slugs for unmapped provider IDs."""
    aliases = load_aliases(config)
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
