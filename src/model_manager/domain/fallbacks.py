"""Generate LiteLLM fallbacks YAML from models.json, tier tags, and provider scans."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from model_manager.config import AppConfig
from model_manager.domain import storage, tags
from model_manager.domain.yaml_gen import (
    _derive_model_name,
    _iter_provider_ids,
    _load_scan_results,
    dump_litellm_yaml,
)

log = logging.getLogger(__name__)

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
    ``details`` maps each model_name to its provider/status/availability so
    callers can rank models globally. Provider-map metadata flags (e.g.
    ``include_in_litellm``) are never treated as model IDs.
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
        model_names: list[tuple[int, float, str, str]] = []

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
                    pname,
                    _derive_model_name(pc.litellm_prefix, pid),
                ))

        if not model_names:
            continue

        model_names.sort(key=lambda e: (e[0], -e[1]))
        tier = _tier_of(info)
        groups[key] = {
            "tier": tier,
            "composite": comp,
            "model_names": [mn for _, _, _, mn in model_names],
            # Per-model metadata used for global DAG ranking. Same
            # derivation loop as generate_provider_yaml, so these names
            # are exactly what lands in the per-provider model_list files.
            "details": {
                mn: {
                    "provider": pname,
                    "status_rank": srank,
                    "availability": avail,
                    "composite": comp,
                    "tier": tier,
                }
                for srank, avail, pname, mn in model_names
            },
        }

    return groups


# Tier weight for global ranking (lower = better). Unknown tiers sort last.
_TIER_WEIGHT = {"tier-1": 0, "tier-2": 1, "tier-3": 2}


def _tier_alias_attr(tier: str | None) -> str:
    """Map a ``tier-N`` tag to the ``[tier_providers.<tier>]`` attribute."""
    from model_manager.domain.model_group_aliases import TIER_ALIAS_KEYS

    if tier in TIER_ALIAS_KEYS:
        return TIER_ALIAS_KEYS[tier]
    return "tier2"


def _global_rank_table(
    config: AppConfig, groups: dict[str, dict]
) -> dict[str, tuple]:
    """Rank every model_name so fallback edges only point 'downhill'.

    Reuses the alias priority (``[tier_providers.<tier>]`` provider order
    first, variant composite second, status/availability as tiebreaks):

        (tier_weight, provider_index, -composite, status_rank,
         -availability, model_name)
    """
    from model_manager.domain.model_group_aliases import _provider_order

    orders = {
        attr: _provider_order(config, attr)
        for attr in ("tier1", "tier2", "tier3")
    }
    ranks: dict[str, tuple] = {}
    for group in groups.values():
        for mn, det in group["details"].items():
            tier = det["tier"]
            order = orders[_tier_alias_attr(tier)]
            try:
                pidx = order.index(det["provider"])
            except ValueError:
                pidx = len(order)
            ranks[mn] = (
                _TIER_WEIGHT.get(tier, 3),
                pidx,
                -det["composite"],
                det["status_rank"],
                -det["availability"],
                mn,
            )
    return ranks


def collect_valid_model_names(config: AppConfig) -> set[str]:
    """Return every model_name the generated YAML may legally reference.

    Union of all ``model_list`` model_names (same derivation loop as
    ``generate_provider_yaml``) plus the ``model_group_alias`` keys and
    the concrete model_names they point at.
    """
    groups = _collect_variants(config)
    valid = {mn for g in groups.values() for mn in g["model_names"]}
    try:
        from model_manager.domain.model_group_aliases import build_alias_map

        aliases = build_alias_map(config)
    except RuntimeError:
        aliases = {}
    valid.update(aliases.keys())
    valid.update(aliases.values())
    return valid


def _as_adjacency(
    fallback_list: list[dict[str, list[str]]],
) -> tuple[dict[str, list[str]], list[int], list[str]]:
    """Split a fallbacks list into adjacency + malformed indices + dup subjects."""
    adj: dict[str, list[str]] = {}
    malformed: list[int] = []
    duplicates: list[str] = []
    for i, item in enumerate(fallback_list):
        if not isinstance(item, dict) or len(item) != 1:
            malformed.append(i)
            continue
        subject = next(iter(item))
        targets = item[subject]
        if not isinstance(targets, list) or not all(
            isinstance(t, str) for t in targets
        ):
            malformed.append(i)
            continue
        if subject in adj:
            if subject not in duplicates:
                duplicates.append(subject)
            adj[subject] = adj[subject] + list(targets)
        else:
            adj[subject] = list(targets)
    return adj, malformed, duplicates


def validate_fallbacks(
    fallback_list: list[dict[str, list[str]]],
    valid_names: set[str] | None = None,
) -> dict[str, Any]:
    """Scan a fallback map for structural and reference problems.

    Returns ``{"unknown_names": [...], "malformed": [...], "self_edges":
    [...], "duplicate_subjects": [...], "cycles": [...]}`` where
    ``malformed`` holds the list indices of entries that are not
    single-key ``{str: [str, ...]}`` dicts.
    """
    adj, malformed, duplicates = _as_adjacency(fallback_list)
    unknown: set[str] = set()
    if valid_names is not None:
        for subject, targets in adj.items():
            if subject not in valid_names:
                unknown.add(subject)
            unknown.update(t for t in targets if t not in valid_names)
    self_edges = sorted(
        s for s, targets in adj.items() if s in targets
    )
    return {
        "unknown_names": sorted(unknown),
        "malformed": malformed,
        "self_edges": self_edges,
        "duplicate_subjects": duplicates,
        "cycles": find_cycles(fallback_list),
    }
    unknown: set[str] = set()
    if valid_names is not None:
        for subject, targets in adj.items():
            if subject not in valid_names:
                unknown.add(subject)
            unknown.update(t for t in targets if t not in valid_names)
    self_edges = sorted(
        s for s, targets in adj.items() if s in targets
    )
    return {
        "unknown_names": sorted(unknown),
        "malformed": malformed,
        "self_edges": self_edges,
        "cycles": find_cycles(fallback_list),
    }


def find_cycles(
    fallback_list: list[dict[str, list[str]]],
) -> list[list[str]]:
    """Return cycles (direct and indirect) in a fallback map.

    Each cycle is reported as ``[n1, n2, ..., n1]``. Empty list means the
    map is a DAG. Malformed entries are ignored.
    """
    adj, _, _ = _as_adjacency(fallback_list)
    cycles: list[list[str]] = []
    visited: dict[str, int] = {}  # 0=unseen 1=in-stack 2=done
    stack: list[str] = []

    def dfs(node: str) -> None:
        visited[node] = 1
        stack.append(node)
        for nxt in adj.get(node, []):
            if nxt == node:
                cycles.append([node, node])
                continue
            state = visited.get(nxt, 0)
            if state == 1:
                cycles.append(stack[stack.index(nxt):] + [nxt])
            elif state == 0 and nxt in adj:
                dfs(nxt)
        stack.pop()
        visited[node] = 2

    for subject in adj:
        if visited.get(subject, 0) == 0:
            dfs(subject)
    return cycles


def is_dag(fallback_list: list[dict[str, list[str]]]) -> bool:
    """Return True when a fallback map has no circular dependencies."""
    return not find_cycles(fallback_list)


def assert_valid_fallbacks(
    fallback_list: list[dict[str, list[str]]],
    valid_names: set[str] | None = None,
) -> None:
    """Fail loudly if a fallback map is not safe for LiteLLM.

    Raises RuntimeError listing unknown names, malformed entries,
    self-edges, or cycles. Call on the final list just before
    serializing so a sanitizer bug can never ship bad YAML silently.
    """
    report = validate_fallbacks(fallback_list, valid_names)
    problems = []
    if report["unknown_names"]:
        problems.append(f"unregistered names: {report['unknown_names']}")
    if report["malformed"]:
        problems.append(f"malformed entries at indices: {report['malformed']}")
    if report["self_edges"]:
        problems.append(f"self-edges: {report['self_edges']}")
    if report["cycles"]:
        problems.append(f"cycles: {report['cycles']}")
    if problems:
        raise RuntimeError("Invalid fallbacks map: " + "; ".join(problems))


def sanitize_fallbacks(
    fallback_list: list[dict[str, list[str]]],
    valid_names: set[str] | None = None,
) -> list[dict[str, list[str]]]:
    """Return a clean ``List[Dict[str, List[str]]]`` fallback map.

    Drops malformed entries, self-edges, and references to names outside
    *valid_names*; removes empty lists; then deletes back-edges that would
    close a cycle (order-preserving, so earlier entries win). The result
    is always a DAG of single-key dicts.
    """
    adj, _, _ = _as_adjacency(fallback_list)
    # Filter + drop self-edges and empties.
    clean: dict[str, list[str]] = {}
    order: list[str] = []
    for item in fallback_list:
        if not isinstance(item, dict) or len(item) != 1:
            log.warning("Dropping malformed fallbacks entry: %r", item)
            continue
        subject = next(iter(item))
        targets = item[subject]
        if not isinstance(targets, list):
            log.warning("Dropping malformed fallbacks entry for %r", subject)
            continue
        kept = [t for t in targets if isinstance(t, str) and t != subject]
        if valid_names is not None:
            if subject not in valid_names:
                log.warning("Dropping fallbacks subject %r: not in model_list", subject)
                continue
            before = len(kept)
            kept = [t for t in kept if t in valid_names]
            if len(kept) != before:
                log.warning(
                    "Dropping %d unregistered fallback target(s) for %r",
                    before - len(kept), subject,
                )
        # De-dupe, preserving order.
        deduped = list(dict.fromkeys(kept))
        if not deduped:
            continue
        if subject in clean:
            merged = list(dict.fromkeys(clean[subject] + deduped))
            clean[subject] = merged
        else:
            clean[subject] = deduped
            order.append(subject)

    # Break cycles: keep edge subject->target only if target cannot already
    # reach subject through kept edges (order-preserving DFS per subject).
    kept_adj: dict[str, list[str]] = {}

    def reaches(src: str, dst: str) -> bool:
        seen = {src}
        work = [src]
        while work:
            node = work.pop()
            for nxt in kept_adj.get(node, []):
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    work.append(nxt)
        return False

    for subject in order:
        kept_targets = []
        for target in clean[subject]:
            if target in kept_adj and reaches(target, subject):
                log.warning(
                    "Dropping fallback edge %r -> %r: would create a cycle",
                    subject, target,
                )
                continue
            kept_targets.append(target)
        if kept_targets:
            kept_adj[subject] = kept_targets

    return [{s: kept_adj[s]} for s in order if s in kept_adj]


def build_fallbacks(config: AppConfig, limit: int = 5) -> list[dict[str, list[str]]]:
    """Build DAG fallback assignments keyed on each provider's model_name.

    For each LiteLLM-included variant, every provider-backed model_name maps
    to a prioritized list of fallbacks: first the same variant served by
    other providers (status-ordered), then same-tier variants ordered by the
    alias priority (``[tier_providers.<tier>]`` provider order, then
    composite), capped at *limit* entries.

    Edges only point "downhill" in the global rank, so the output is a
    directed acyclic graph: if A lists B, B never lists A or any ancestor
    of A. Targets outside the generated ``model_list``/aliases are omitted,
    as are subjects left with no valid downhill target.
    """
    from model_manager.domain.model_group_aliases import _provider_order

    groups = _collect_variants(config)
    ranks = _global_rank_table(config, groups)
    valid = collect_valid_model_names(config)

    orders = {
        attr: _provider_order(config, attr)
        for attr in ("tier1", "tier2", "tier3")
    }

    def provider_index(tier: str | None, provider: str) -> int:
        order = orders[_tier_alias_attr(tier)]
        try:
            return order.index(provider)
        except ValueError:
            return len(order)

    same_tier_names: dict[str, list[str]] = {}
    for subject_key, subject in groups.items():
        tier = subject["tier"]
        same_tier: list[tuple[tuple, str]] = []
        for other_key, other in groups.items():
            if other_key == subject_key:
                continue
            if tier and other["tier"] == tier:
                best = other["model_names"][0]
                det = other["details"][best]
                same_tier.append((
                    (
                        provider_index(other["tier"], det["provider"]),
                        -other["composite"],
                        det["status_rank"],
                        -det["availability"],
                        best,
                    ),
                    best,
                ))
        same_tier.sort(key=lambda e: e[0])
        same_tier_names[subject_key] = [mn for _, mn in same_tier]

    raw: list[dict[str, list[str]]] = []
    for subject_key, subject in groups.items():
        tier_names = same_tier_names[subject_key]

        for subject_name in subject["model_names"]:
            other_same = [mn for mn in subject["model_names"] if mn != subject_name]
            ordered = other_same + list(tier_names)
            # Keep only registered names, strictly downhill in global rank.
            srank = ranks[subject_name]
            downhill = [
                n for n in ordered
                if n != subject_name and n in valid and ranks[n] > srank
            ]
            fallback_list = downhill[:limit]
            if fallback_list:
                raw.append({subject_name: fallback_list})

    return sanitize_fallbacks(raw, valid)


def generate_fallbacks_yaml(
    config: AppConfig,
    *,
    dry_run: bool = False,
    output_path: Path | None = None,
    limit: int = 5,
) -> str | None:
    """Serialize fallbacks to YAML. Returns the string on dry_run, else writes a file.

    The map is validated just before serializing; any residual cycle,
    unregistered name, or malformed entry raises RuntimeError instead of
    shipping YAML LiteLLM would drop.
    """
    built = build_fallbacks(config, limit=limit)
    assert_valid_fallbacks(built, collect_valid_model_names(config))

    yaml_doc = dump_litellm_yaml({"fallbacks": built})

    if dry_run:
        return yaml_doc

    out_path = output_path or config.litellm_fallbacks_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None
