"""Generate LiteLLM fallbacks YAML from models.json, tier tags, and provider scans."""
from __future__ import annotations

from typing import Any

import yaml

from model_manager.config import AppConfig
from model_manager.domain import storage, tags
from model_manager.domain.yaml_gen import (
    _derive_model_name,
    _iter_provider_ids,
    _load_scan_results,
)

# Assessment priority, lowest = best. Unauthorized entries are excluded entirely.
STATUS_RANK = {
    "active": 0,
    "good": 1,
    "ratelimited": 2,
    "slow": 3,
    "weak": 4,
    "dead": 5,
}
UNKNOWN_RANK = 6


def status_rank(assessment: str | None) -> int:
    """Map an assessment string to its ordering rank (lower is preferred)."""
    if not assessment:
        return UNKNOWN_RANK
    return STATUS_RANK.get(assessment.strip().lower(), UNKNOWN_RANK)


def _tier_of(variant_info: dict) -> str | None:
    for tag in variant_info.get("tags", []):
        if tag.startswith(tags.TIER_TAG_PREFIX):
            return tag
    return None


def _collect_variants(config: AppConfig) -> dict[str, dict]:
    """Return {variant_key: group} for every LiteLLM-included scored variant.

    Each group holds the variant's tier and composite plus its fully-qualified
    model_names (one per configured provider entry), sorted by status rank then
    availability, and cross-checked against provider scan files for exclusions.
    """
    data = storage.load_models_data(config)
    providers = {
        name: pc
        for name, pc in config.providers.items()
        if pc.keys and pc.litellm_prefix
    }
    scans = {
        name: _load_scan_results(config, name)
        for name in providers
    }

    groups: dict[str, dict] = {}
    for key, v in tags.variant_scores(data).items():
        info = v["info"]
        if info.get("include_in_litellm") is False:
            continue

        comp = v["composite"]
        if comp is None:
            continue

        prov_map = info.get("provider_ids", {})
        model_names: list[tuple[int, float, str]] = []

        for pname, pc in providers.items():
            pmap = prov_map.get(pname) or prov_map.get(pname.capitalize())
            if not isinstance(pmap, dict):
                continue
            if pmap.get("include_in_litellm") is False:
                continue

            for pid in _iter_provider_ids(pmap):
                entry = pmap[pid]
                assessment = entry.get("assessment") if isinstance(entry, dict) else None
                if (assessment or "").strip().lower() == "unauthorized":
                    continue
                if scans[pname].get(pid) == "unauthorized":
                    continue

                availability = entry.get("availability") if isinstance(entry, dict) else None
                if not isinstance(availability, (int, float)):
                    availability = 0.0

                model_names.append((
                    status_rank(assessment),
                    float(availability),
                    _derive_model_name(pc.litellm_prefix, pid),
                ))

        if not model_names:
            continue

        model_names.sort(key=lambda e: (e[0], -e[1]))
        groups[key] = {
            "tier": _tier_of(info),
            "composite": comp,
            "model_names": [mn for _, _, mn in model_names],
        }

    return groups


def build_fallbacks(config: AppConfig, limit: int = 5) -> list[dict[str, list[str]]]:
    """Build fallback assignments keyed on each provider's full model_name.

    For each LiteLLM-included variant, every provider-backed model_name maps to a
    prioritized list of fallbacks: first the same variant served by other providers
    (status-ordered), then same-tier variants (composite-ordered, each via its best
    provider), capped at *limit* entries.
    """
    groups = _collect_variants(config)

    same_tier_names: dict[str, list[str]] = {}
    for subject_key, subject in groups.items():
        tier = subject["tier"]
        same_tier: list[tuple[float, str]] = []
        for other_key, other in groups.items():
            if other_key == subject_key:
                continue
            if tier and other["tier"] == tier:
                same_tier.append((other["composite"], other["model_names"][0]))
        same_tier.sort(key=lambda e: -e[0])
        same_tier_names[subject_key] = [mn for _, mn in same_tier]

    fallbacks: list[dict[str, list[str]]] = []
    for subject_key, subject in groups.items():
        tier_names = same_tier_names[subject_key]

        for subject_name in subject["model_names"]:
            other_same = [mn for mn in subject["model_names"] if mn != subject_name]
            ordered = other_same + list(tier_names)
            fallback_list = [n for n in ordered if n != subject_name][:limit]
            if fallback_list:
                fallbacks.append({subject_name: fallback_list})

    return fallbacks


def generate_fallbacks_yaml(
    config: AppConfig,
    *,
    dry_run: bool = False,
    output_path: Path | None = None,
    limit: int = 5,
) -> str | None:
    """Serialize fallbacks to YAML. Returns the string on dry_run, else writes a file."""
    fallbacks = build_fallbacks(config, limit=limit)

    yaml_doc = yaml.safe_dump(
        {"fallbacks": fallbacks},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    if dry_run:
        return yaml_doc

    out_path = output_path or config.litellm_fallbacks_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None
