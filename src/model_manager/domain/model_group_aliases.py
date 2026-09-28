"""Generate LiteLLM model_group_alias YAML from tier tags and provider hierarchy."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from model_manager.config import AppConfig
from model_manager.domain import blocks, storage, tags
from model_manager.domain.yaml_gen import (
    _derive_model_name,
    _iter_provider_ids,
    dump_litellm_yaml,
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
    blocked = blocks.blocked_set(config)
    # Band only the live variants so the leader and ratio cutoffs track
    # what can actually be served; blocked/excluded variants keep no say
    # in where the tier boundaries fall.
    tier_map = tags.compute_tiers(
        data,
        t1_ratio=config.tags.tier1_min_ratio,
        t2_ratio=config.tags.tier2_min_ratio,
        only=tags.live_variant_keys(data, config, blocked),
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
                model_name = _derive_model_name(pc.litellm_prefix, pid)
                if blocks.provider_key(pname, pid) in blocked:
                    continue
                if f"litellm:{model_name}" in blocked:
                    continue
                availability = entry.get("availability") if isinstance(entry, dict) else None
                if not isinstance(availability, (int, float)):
                    availability = 0.0
                ranked.append((
                    status_rank(assessment),
                    float(availability),
                    pname,
                    model_name,
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


def _pick_best(
    candidates: list[dict[str, Any]],
    order: list[str],
    blocked: set[str],
) -> str | None:
    """Pick the best candidate target honoring provider order, else None."""
    for pname in order:
        backed = [c for c in candidates if pname in c["names"]]
        backed.sort(key=lambda c: (-c["composite"], c["key"]))
        for cand in backed:
            target = cand["names"][pname]
            if f"litellm:{target}" not in blocked:
                return target
    ordered = sorted(candidates, key=lambda c: (-c["composite"], c["key"]))
    for cand in ordered:
        if f"litellm:{cand['best']}" not in blocked:
            return cand["best"]
    return None


def build_alias_map(config: AppConfig) -> dict[str, str]:
    """Build ``{alias: model_name}`` with tier purity over provider preference.

    Candidates are filtered to their own tier first; the per-tier provider
    order then picks which in-tier variant wins. Tiers without eligible
    variants cascade: a missing tier inherits the nearest resolved alias
    (tier2 falls back to tier1, tier3 to tier2), so one live model backs
    all three aliases and two live models cover tier1 plus tier2/tier3.
    A single-tagged model may therefore back more than one alias. Only
    when no tier has any eligible variant is the map empty.
    """
    grouped = _collect_candidates(config)

    blocked = blocks.blocked_set(config)
    picks: dict[str, str | None] = {}
    for tier_tag, alias_key in TIER_ALIAS_KEYS.items():
        order = _provider_order(config, alias_key)
        picks[alias_key] = _pick_best(grouped.get(tier_tag, []), order, blocked)

    tier1 = picks["tier1"] or picks["tier2"] or picks["tier3"]
    tier2 = picks["tier2"] or tier1
    tier3 = picks["tier3"] or tier2
    resolved = {"tier1": tier1, "tier2": tier2, "tier3": tier3}

    alias_map: dict[str, str] = {}
    for alias_key in ("tier1", "tier2", "tier3"):
        picked = resolved[alias_key]
        if picked is None:
            log.warning("No eligible variants for %s; omitting alias.", alias_key)
            continue
        if picked != picks[alias_key]:
            log.warning(
                "No eligible %s variant; cascading to %s.",
                alias_key, picked,
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

    yaml_doc = dump_litellm_yaml({"model_group_alias": alias_map})

    if dry_run:
        return yaml_doc

    out_path = output_path or config.litellm_aliases_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None
