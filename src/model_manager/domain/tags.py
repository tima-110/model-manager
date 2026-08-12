"""Tier classification and tag management for conceptual model variants."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from model_manager.config import AppConfig
from model_manager.domain import storage

TIER_TAG_PREFIX = "tier-"


def composite_score(scores: dict | None) -> float | None:
    """Average of intelligence + coding + agentic; falls back to whichever are numeric."""
    if not scores:
        return None
    numeric = [scores[k] for k in ("intelligence", "coding", "agentic") if scores.get(k) is not None]
    if not numeric:
        return None
    return sum(numeric) / len(numeric)


def variant_scores(models_data: dict) -> dict[str, dict]:
    """Flatten all scored variants into {variant_key: variant_info}."""
    flat: dict[str, dict] = {}
    for mid, m_info in models_data.get("models", {}).items():
        for vid, v_info in m_info.get("variants", {}).items():
            comp = composite_score(v_info.get("scores"))
            flat[f"{mid}/{vid}"] = {"model": mid, "variant": vid, "info": v_info, "composite": comp}
    return flat


def compute_tiers(models_data: dict, t1_ratio: float = 0.85, t2_ratio: float = 0.70) -> dict[str, str]:
    """Return {variant_key: tier} using relative-to-leader bands.

    Leader is the highest composite score in the library. Variants whose
    composite is >= ``t1_ratio`` of the leader get tier-1, >= ``t2_ratio``
    get tier-2, and the rest get tier-3. Variants without a composite score
    are omitted.
    """
    flat = variant_scores(models_data)
    comps = [v["composite"] for v in flat.values() if v["composite"] is not None]
    if not comps:
        return {}
    leader = max(comps)

    tiers: dict[str, str] = {}
    for key, v in flat.items():
        if v["composite"] is None:
            continue
        if v["composite"] >= leader * t1_ratio:
            tiers[key] = "tier-1"
        elif v["composite"] >= leader * t2_ratio:
            tiers[key] = "tier-2"
        else:
            tiers[key] = "tier-3"
    return tiers


def _replace_tier_tags(tags: list[str], tier: str) -> list[str]:
    """Drop all tier-* tags and append the new tier tag. Preserves manual tags."""
    clean = [t for t in tags if not t.startswith(TIER_TAG_PREFIX)]
    return clean + [tier]


def assign_tier_tags(config: AppConfig, t1_ratio: float = 0.85, t2_ratio: float = 0.70) -> tuple[int, dict[str, str], float]:
    """Write tier tags into models.json. Returns (updated_count, tier_map, leader)."""
    data = storage.load_models_data(config)
    tiers = compute_tiers(data, t1_ratio=t1_ratio, t2_ratio=t2_ratio)
    flat = variant_scores(data)
    comps = [v["composite"] for v in flat.values() if v["composite"] is not None]
    leader = max(comps) if comps else 0.0

    updated = 0
    for key, tier in tiers.items():
        model_id, variant_id = key.rsplit("/", 1)
        tags = flat[key]["info"].setdefault("tags", [])
        replaced = _replace_tier_tags(tags, tier)
        if replaced != tags:
            flat[key]["info"]["tags"] = replaced
            updated += 1

    if updated:
        if "meta" not in data:
            data["meta"] = {}
        data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        storage.save_models_data(config, data)

    return updated, tiers, leader


def list_tags(config: AppConfig) -> dict[str, list[str]]:
    """Return {model/variant: tags} for all variants with tags."""
    data = storage.load_models_data(config)
    result: dict[str, list[str]] = {}
    for mid, m_info in data.get("models", {}).items():
        for vid, v_info in m_info.get("variants", {}).items():
            tags = v_info.get("tags", [])
            if tags:
                result[f"{mid}/{vid}"] = list(tags)
    return result


def set_tag(config: AppConfig, model_id: str, variant_id: str, tag: str) -> bool:
    """Add a tag to a variant manually. Returns False if the variant is missing."""
    return _update_tags(config, model_id, variant_id, lambda tags: tags if tag in tags else tags + [tag])


def remove_tag(config: AppConfig, model_id: str, variant_id: str, tag: str) -> bool:
    """Remove a tag from a variant manually. Returns False if the variant is missing."""
    return _update_tags(config, model_id, variant_id, lambda tags: [t for t in tags if t != tag])


def _update_tags(config: AppConfig, model_id: str, variant_id: str, transform: Any) -> bool:
    data = storage.load_models_data(config)
    variant = data.get("models", {}).get(model_id, {}).get("variants", {}).get(variant_id)
    if variant is None:
        return False
    tags = variant.setdefault("tags", [])
    new_tags = transform(list(tags))
    if new_tags != tags:
        variant["tags"] = new_tags
        if "meta" not in data:
            data["meta"] = {}
        data["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        storage.save_models_data(config, data)
    return True