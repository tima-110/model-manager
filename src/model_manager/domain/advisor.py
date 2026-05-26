"""High-level advisory logic for model selection and comparison."""
from __future__ import annotations

from typing import Any
from model_manager.config import AppConfig
from model_manager.domain import aliases

def compare_models(config: AppConfig, provider_ids: list[str]) -> list[dict]:
    """
    Fetches and aggregates scores for a list of provider IDs.
    Returns a list of results including the resolved AA slug and scores.
    """
    results = []
    for pid in provider_ids:
        res = aliases.resolve_id(pid, config)
        if res:
            results.append({
                "provider_id": pid,
                "model": res["model"],
                "variant": res["variant"],
                "aa_slug": res["aa_slug"],
                "scores": res["scores"]["scores"] if res["scores"] else {}
            })
        else:
            results.append({
                "provider_id": pid,
                "model": None,
                "variant": None,
                "aa_slug": None,
                "scores": {},
                "error": "No mapping found"
            })
    return results

def find_best_model(config: AppConfig, metric: str = "intelligence") -> dict | None:
    """
    Scans all mapped models to find the one with the highest score for a given metric.
    """
    aliases_data = aliases.load_aliases(config)
    scores_data = aliases.load_json_safe(aliases.get_scores_path(config)) # Wait, fix path

    # Let's use the config helpers
    from model_manager.config import get_scores_path
    scores_data = aliases.load_json_safe(get_scores_path(config))

    best_model = None
    best_score = -1.0

    for model_id, model_data in aliases_data.get("models", {}).items():
        # Check the default variant
        default_var = model_data.get("default_variant")
        if not default_var:
            continue

        variant_data = model_data["variants"].get(default_variant)
        if not variant_data:
            continue

        slug = variant_data.get("aa_slug")
        if not slug:
            continue

        score_entry = scores_data.get("models", {}).get(slug)
        if not score_entry:
            continue

        score = score_entry.get("scores", {}).get(metric)
        if score is not None and score > best_score:
            best_score = score
            best_model = {
                "model": model_id,
                "variant": default_var,
                "slug": slug,
                "score": score
            }

    return best_model

def get_mapping_gaps(config: AppConfig, known_ids: list[str]) -> list[str]:
    """Returns a list of known IDs that are missing an AA mapping."""
    report = aliases.audit_mappings(config, known_ids)
    return report["missing_ids"]
