"""Generate LiteLLM model_group_alias YAML from tier tags and provider hierarchy."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from model_manager.config import AppConfig
from model_manager.domain import storage, tags
from model_manager.domain.yaml_gen import (
    _derive_model_name,
    _iter_provider_ids,
    _load_scan_results,
)

log = logging.getLogger(__name__)

TIER_ALIAS_KEYS: dict[str, str] = {
    "tier-1": "tier1",
    "tier-2": "tier2",
    "tier-3": "tier3",
}

TIER_ATTRS: tuple[str, str, str] = ("tier1", "tier2", "tier3")


def _tier_of(variant_info: dict) -> str | None:
    for tag in variant_info.get("tags", []):
        if tag.startswith(tags.TIER_TAG_PREFIX):
            return tag
    return None


def _provider_order(config: AppConfig, tier_attr: str) -> list[str]:
    """Resolve the provider preference order for one tier.

    Uses the configured ``[tier_providers.<tier>]`` order when present,
    otherwise defaults to nvidia first followed by the remaining configured
    providers. Unknown names are dropped with a warning.
    """
    tier_cfg = getattr(config.tier_providers, tier_attr, None)
    configured = list(getattr(tier_cfg, "provider_order", []) or [])

    available = [
        name for name, pc in config.providers.items()
        if pc.keys and pc.litellm_prefix
    ]
    if configured:
        order = [p for p in configured if p in available]
        unknown = [p for p in configured if p not in available]
        if unknown:
            log.warning(
                "Ignoring unknown providers in [tier_providers.%s]: %s",
                tier_attr, ", ".join(unknown),
            )
        if order:
            return order
    ordered = ["nvidia"] if "nvidia" in available else []
    ordered += [p for p in available if p not in ordered]
    return ordered


def _collect_candidates(config: AppConfig) -> dict[str, list[dict[str, Any]]]:
    """Return ``{tier-tag: [candidate]}`` for LiteLLM-eligible scored variants.

    Each candidate holds its composite score, variant key, per-provider
    model names, and a best-overall model name. Unauthorized and excluded
    entries are skipped, mirroring the fallbacks collector.
    """
    from model_manager.domain.fallbacks import status_rank

    data = storage.load_models_data(config)
    providers = {
        name: pc
        for name, pc in config.providers.items()
        if pc.keys and pc.litellm_prefix
    }
    scans = {name: _load_scan_results(config, name) for name in providers}
    tier_map = tags.compute_tiers(
        data,
        t1_ratio=config.tags.tier1_min_ratio,
        t2_ratio=config.tags.tier2_min_ratio,
    )

    grouped: dict[str, list[dict[str, Any]]] = {t: [] for t in TIER_ALIAS_KEYS}
    for key, v in tags.variant_scores(data).items():
        info = v["info"]
        if info.get("include_in_litellm") is False:
            continue
        comp = v["composite"]
        if comp is None:
            continue

        tier = _tier_of(info) or tier_map.get(key)
        if tier not in grouped:
            continue

        prov_map = info.get("provider_ids", {})
        ranked: list[tuple[int, float, str, str]] = []
        for pname, pc in providers.items():
            pmap = prov_map.get(pname) or prov_map.get(pname.capitalize())
            if pmap is None:
                continue
            if isinstance(pmap, dict) and pmap.get("include_in_litellm") is False:
                continue
            entries = pmap if isinstance(pmap, dict) else {pid: {} for pid in pmap}
            for pid in _iter_provider_ids(pmap):
                entry = entries.get(pid) if isinstance(entries, dict) else None
                assessment = entry.get("assessment") if isinstance(entry, dict) else None
                if (assessment or "").strip().lower() == "unauthorized":
                    continue
                if scans[pname].get(pid) == "unauthorized":
                    continue
                availability = entry.get("availability") if isinstance(entry, dict) else None
                if not isinstance(availability, (int, float)):
                    availability = 0.0
                ranked.append((
                    status_rank(assessment),
                    float(availability),
                    pname,
                    _derive_model_name(pc.litellm_prefix, pid),
                ))

        if not ranked:
            continue
        ranked.sort(key=lambda e: (e[0], -e[1], e[2], e[3]))
        names: dict[str, str] = {}
        for _, _, pname, model_name in ranked:
            names.setdefault(pname, model_name)
        grouped[tier].append({
            "key": key,
            "composite": comp,
            "names": names,
            "best": ranked[0][3],
        })

    return grouped


def build_alias_map(config: AppConfig) -> dict[str, str]:
    """Build ``{alias: model_name}`` with tier purity over provider preference.

    Candidates are filtered to their own tier first; the per-tier provider
    order then picks which in-tier variant wins. A tier with no provider
    from its hierarchy falls back to its best composite on any provider.
    Tiers without eligible variants are omitted.
    """
    grouped = _collect_candidates(config)

    alias_map: dict[str, str] = {}
    for tier_tag, alias_key in TIER_ALIAS_KEYS.items():
        candidates = grouped.get(tier_tag, [])
        if not candidates:
            log.warning("No eligible variants for %s; omitting alias.", alias_key)
            continue
        order = _provider_order(config, alias_key)
        picked: str | None = None
        for pname in order:
            backed = [c for c in candidates if pname in c["names"]]
            if backed:
                backed.sort(key=lambda c: (-c["composite"], c["key"]))
                picked = backed[0]["names"][pname]
                break
        if picked is None:
            candidates.sort(key=lambda c: (-c["composite"], c["key"]))
            picked = candidates[0]["best"]
            log.warning(
                "No %s variant on providers %s; using %s.",
                alias_key, ", ".join(order) or "(none)", picked,
            )
        alias_map[alias_key] = picked

    return alias_map


def generate_aliases_yaml(
    config: AppConfig,
    *,
    dry_run: bool = False,
    output_path: Path | None = None,
) -> str | None:
    """Serialize the alias map to YAML. Returns the string on dry_run, else writes a file."""
    alias_map = build_alias_map(config)
    if not alias_map:
        raise RuntimeError(
            "No aliases generated. Check that models.json has scored, "
            "LiteLLM-included variants with mapped provider_ids."
        )

    yaml_doc = yaml.safe_dump(
        {"model_group_alias": alias_map},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    if dry_run:
        return yaml_doc

    out_path = output_path or config.litellm_aliases_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None
