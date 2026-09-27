"""Persistent availability ledger for generated configs.

Fetch and scan observations record which targets are unusable; the three
generators consult this ledger (plus the permanent user exclusions) to
decide what to emit. Which signals block is user-tunable via the
``[blocking]`` config section; the first clean observation releases a
target. ``models.json`` mappings are never modified here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_manager.config import AppConfig
from model_manager.domain import storage

LEDGER_FILENAME = "model_blocks.json"

# Assessment labels that mean "reachable, keep emitting".
_HEALTHY_LABELS = frozenset({"good", "slow", "weak"})


def normalize_signal(token: str | None) -> str:
    """Normalize a status/code token for block-list comparison."""
    return (token or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_block_signal(token: str | None, config: AppConfig) -> bool:
    """Return True when an observed signal blocks generation."""
    allowed = {normalize_signal(s) for s in config.blocking.block_signals}
    return normalize_signal(token) in allowed


def ledger_path(config: AppConfig) -> Path:
    return config.data_dir / LEDGER_FILENAME


def load_ledger(config: AppConfig) -> dict[str, dict[str, Any]]:
    """Load the ledger; missing/corrupt files read as empty (never raise)."""
    path = ledger_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ledger(config: AppConfig, ledger: dict[str, dict[str, Any]]) -> None:
    ledger_path(config).write_text(json.dumps(ledger, indent=2))


def provider_key(provider: str, pid: str) -> str:
    return f"provider:{provider.lower()}/{pid}"


def litellm_keys(name: str) -> list[str]:
    return [f"litellm:{name}"]


def alias_keys(name: str) -> list[str]:
    return [f"alias:{name}", f"litellm:{name}"]


def derived_keys(config: AppConfig, provider: str, pid: str) -> list[str]:
    """All ledger keys covering one (provider, pid) mapping."""
    from model_manager.domain.yaml_gen import _derive_model_name

    keys = [provider_key(provider, pid)]
    pc = config.providers.get(provider) or config.providers.get(provider.lower())
    prefix = getattr(pc, "litellm_prefix", "") if pc else ""
    if prefix:
        keys.append(f"litellm:{_derive_model_name(prefix, pid)}")
    return keys


def blocked_set(config: AppConfig) -> set[str]:
    """Return the set of currently blocked ledger keys (one file read)."""
    return set(load_ledger(config).keys())


def is_blocked(ledger: dict[str, dict[str, Any]], keys: list[str]) -> dict[str, Any] | None:
    """Return the first matching block entry, or None."""
    for key in keys:
        if key in ledger:
            return ledger[key]
    return None


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_observation(
    config: AppConfig,
    keys: list[str],
    *,
    blocked: bool,
    reason: str,
    code: str | None = None,
    source: str = "",
) -> None:
    """Block or release ledger keys. Blocking preserves the first timestamp."""
    ledger = load_ledger(config)
    if blocked:
        for key in keys:
            existing = ledger.get(key, {})
            ledger[key] = {
                "blocked_since": existing.get("blocked_since", _stamp()),
                "reason": reason,
                "code": code,
                "source": source,
            }
    else:
        for key in keys:
            ledger.pop(key, None)
    save_ledger(config, ledger)


def _mapped_pids(config: AppConfig, provider: str) -> set[str]:
    """All provider IDs mapped in models.json for a provider (any case)."""
    pids: set[str] = set()
    try:
        models_data = storage.load_models_data(config)
    except Exception:
        return pids
    for model_info in models_data.get("models", {}).values():
        for variant_info in model_info.get("variants", {}).values():
            prov_map = variant_info.get("provider_ids", {}) or {}
            pmap = prov_map.get(provider) or prov_map.get(provider.lower())
            if isinstance(pmap, dict):
                pids.update(k for k in pmap if not k.startswith("include_"))
    return pids


def record_fetch_observations(
    config: AppConfig, provider: str, fetched_ids: set[str]
) -> dict[str, int]:
    """Block mapped IDs absent from a fresh fetch; release present ones.

    Fetch-absence always blocks (structural staleness, not signal-based).
    An empty fetch result records nothing: absence of evidence is not
    evidence of absence (API hiccup vs. empty provider).
    Returns {"blocked": n, "released": n}.
    """
    fetched = {str(i) for i in fetched_ids}
    if not fetched:
        return {"blocked": 0, "released": 0}
    ledger = load_ledger(config)
    blocked = released = 0
    changed = False
    for pid in _mapped_pids(config, provider):
        keys = derived_keys(config, provider, pid)
        if pid in fetched:
            for key in keys:
                if ledger.pop(key, None) is not None:
                    released += 1
                    changed = True
        else:
            for key in keys:
                pre_existed = key in ledger
                existing = ledger.get(key, {})
                ledger[key] = {
                    "blocked_since": existing.get("blocked_since", _stamp()),
                    "reason": "absent from provider fetch",
                    "code": None,
                    "source": "fetch",
                }
                if not pre_existed:
                    changed = True
            blocked += 1
    if changed:
        save_ledger(config, ledger)
    return {"blocked": blocked, "released": released}


def record_assessment_observations(
    config: AppConfig, provider: str, assessments: dict[str, str]
) -> dict[str, int]:
    """Fold per-model scan assessments into the ledger.

    Healthy labels release; block-listed signals (unauthorized, not_found,
    unsupported, ...) block; anything else (dead, ratelimited, unknown) is
    track-only and leaves the ledger untouched.
    """
    blocked = released = 0
    for pid, label in assessments.items():
        keys = derived_keys(config, provider, pid)
        signal = normalize_signal(label)
        if signal in _HEALTHY_LABELS:
            record_observation(config, keys, blocked=False, reason="")
            released += 1
        elif is_block_signal(label, config):
            record_observation(
                config, keys, blocked=True,
                reason=f"scan assessment: {label}", code=None, source="scan",
            )
            blocked += 1
    return {"blocked": blocked, "released": released}


def record_probe_observations(
    config: AppConfig, records: list[dict[str, Any]]
) -> dict[str, int]:
    """Fold LiteLLM proxy probe records into the ledger.

    ``up`` releases; block-listed statuses/codes block; everything else
    (timeouts, 500s, empties, rate limits) is track-only.
    """
    blocked = released = 0
    for rec in records:
        name = rec.get("model", "")
        keys = alias_keys(name) if rec.get("kind") == "alias" else litellm_keys(name)
        status, code = rec.get("status"), rec.get("code")
        if status == "up":
            record_observation(config, keys, blocked=False, reason="")
            released += 1
        elif is_block_signal(status, config) or is_block_signal(code, config):
            record_observation(
                config, keys, blocked=True,
                reason=f"proxy probe: {status}", code=code, source="proxy",
            )
            blocked += 1
    return {"blocked": blocked, "released": released}


def blocked_summary(config: AppConfig) -> list[dict[str, Any]]:
    """Ledger entries as a sorted list for doctor/dashboard display."""
    return [
        {"key": key, **entry}
        for key, entry in sorted(load_ledger(config).items())
    ]
