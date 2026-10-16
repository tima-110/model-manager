"""Local model price library used by the radar view.

``model_prices.json`` maps a lowercased lookup key (AA slug or provider
model id) to a blended USD price per 1M tokens plus its source. Sources
merge in this precedence (first wins):

1. ``litellm_cost_overrides.json`` (local, explicit operator data)
2. OpenRouter full model catalog (public ``/api/v1/models`` pricing)
3. Upstream LiteLLM cost map (``input/output_cost_per_token``)

Radar never fetches prices itself; it only reads this file when present.
Use ``prices fetch`` to (re)build it.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_manager.config import AppConfig, get_litellm_cost_map_output_path, get_prices_path

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _blended_per_1m(prompt_per_token: Any, completion_per_token: Any) -> float | None:
    """Return blended USD/1M tokens for a prompt/completion pair, or None."""
    try:
        p = float(prompt_per_token)
        c = float(completion_per_token)
    except (TypeError, ValueError):
        return None
    if p < 0 or c < 0:
        return None
    return round((p + c) / 2 * 1_000_000, 4)


def fetch_openrouter_prices() -> dict[str, dict]:
    """Fetch blended prices for every OpenRouter model (paid and free).

    Returns ``{lowercased_id: {"blended_per_1m": float, "source": "openrouter"}}``.
    Free models map to 0.0. Raises RuntimeError on fetch/parse failure.
    """
    try:
        req = urllib.request.Request(OPENROUTER_MODELS_URL)
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception as e:
        raise RuntimeError(f"Failed to fetch OpenRouter model catalog: {e}") from e
    entries: dict[str, dict] = {}
    for m in payload.get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        pricing = m.get("pricing", {})
        blended = _blended_per_1m(pricing.get("prompt"), pricing.get("completion"))
        if blended is None:
            continue
        entries[str(mid).lower()] = {"blended_per_1m": blended, "source": "openrouter"}
    return entries


def load_prices_from_cost_map(
    cost_map: dict[str, Any],
    source: str = "litellm-local",
) -> dict[str, dict]:
    """Parse blended USD/1M token prices from a LiteLLM cost map dictionary.

    Keys are lowercased model identifiers. If a model identifier contains a slash
    (e.g., 'openai/gpt-4o' or 'bedrock/anthropic.claude-3-5-sonnet'), the unprefixed
    name is also indexed if not already present.
    """
    entries: dict[str, dict] = {}
    for key, block in cost_map.items():
        if not isinstance(block, dict) or key == "sample_spec":
            continue
        blended = _blended_per_1m(
            block.get("input_cost_per_token"), block.get("output_cost_per_token")
        )
        if blended is None:
            continue
        kl = str(key).lower()
        entries[kl] = {"blended_per_1m": blended, "source": source}
        if "/" in kl:
            suffix = kl.split("/", 1)[1]
            if suffix not in entries:
                entries[suffix] = {"blended_per_1m": blended, "source": source}
    return entries


def load_local_cost_map_prices(config: AppConfig) -> dict[str, dict]:
    """Load prices from the generated LiteLLM cost map output file."""
    path = get_litellm_cost_map_output_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return load_prices_from_cost_map(data, source="litellm-local")
    except Exception:
        pass
    return {}


def fetch_upstream_cost_entries(url: str) -> dict[str, dict]:
    """Fetch blended prices from the upstream LiteLLM cost map URL.

    Returns ``{lowercased_key: {"blended_per_1m": float, "source": "litellm-upstream"}}``.
    Raises RuntimeError on failure.
    """
    from model_manager.domain import cost_map

    upstream = cost_map.fetch_upstream_cost_map(url)
    return load_prices_from_cost_map(upstream, source="litellm-upstream")


def load_radar_prices(config: AppConfig, fetch_upstream_if_missing: bool = True) -> dict[str, dict]:
    """Load model prices for the radar view without using the local overrides file.

    Sources prices in precedence order:
    1. Local generated LiteLLM cost map output file (e.g.
       /var/www/local_json_data/model_prices_and_context_window.json).
    2. Upstream LiteLLM cost map catalog (if local output file is absent and network is available).
    3. Cached model price library (model_prices.json), ignoring any 'override' entries.
    """
    prices: dict[str, dict] = {}

    # 1. Local cost map output file
    prices = load_local_cost_map_prices(config)

    # 2. Upstream LiteLLM catalog fallback if local output file is absent
    if not prices and fetch_upstream_if_missing:
        cached_lib = load_prices(config)
        if cached_lib:
            prices = cached_lib
        else:
            try:
                prices = fetch_upstream_cost_entries(config.litellm_cost_map_url)
            except Exception:
                prices = {}
    elif prices:
        # Merge supplemental non-override cached prices (e.g. openrouter)
        cached_lib = load_prices(config)
        clean_cached = {
            k: v for k, v in cached_lib.items()
            if isinstance(v, dict) and v.get("source") != "override"
        }
        prices = merge_entries(prices, clean_cached)

    if not prices:
        # Final fallback to cached library if any exists (e.g. in test fixtures)
        prices = load_prices(config)

    return prices


def load_overrides_entries(config: AppConfig) -> dict[str, dict]:
    """Read local cost overrides and return price entries (first precedence)."""
    from model_manager.domain import cost_map

    overrides = cost_map.load_overrides(config)
    entries: dict[str, dict] = {}
    for key, block in overrides.items():
        if not isinstance(block, dict):
            continue
        if key == "_meta":
            continue
        blended = _blended_per_1m(
            block.get("input_cost_per_token"), block.get("output_cost_per_token")
        )
        if blended is None:
            # Support a directly-authored blended value for hand-written entries.
            try:
                blended = round(float(block.get("blended_per_1m")), 4)
            except (TypeError, ValueError):
                continue
        entries[str(key).lower()] = {"blended_per_1m": blended, "source": "override"}
    return entries


def merge_entries(*layers: dict[str, dict]) -> dict[str, dict]:
    """Merge price layers; earlier layers win on key conflicts."""
    ordered: dict[str, dict] = {}
    for layer in layers:
        for k, v in layer.items():
            if k not in ordered:
                ordered[k] = v
    return ordered


def load_prices(config: AppConfig) -> dict[str, dict]:
    """Return the saved price library (``{key: entry}``), or {} when absent."""
    path = get_prices_path(config)
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    prices = doc.get("prices", {})
    return prices if isinstance(prices, dict) else {}


def lookup_price(keys: list[str], prices: dict[str, dict]) -> float | None:
    """Return the blended price for the first matching key (case-insensitive)."""
    for key in keys:
        if not key:
            continue
        entry = prices.get(str(key).lower())
        if isinstance(entry, dict) and entry.get("blended_per_1m") is not None:
            try:
                return float(entry["blended_per_1m"])
            except (TypeError, ValueError):
                continue
    return None


def build_price_library(
    config: AppConfig,
    *,
    openrouter_entries: dict[str, dict] | None = None,
    upstream_entries: dict[str, dict] | None = None,
    source_url: str | None = None,
    skip_openrouter: bool = False,
    skip_upstream: bool = False,
) -> Path:
    """Build ``model_prices.json`` from overrides + OpenRouter + upstream.

    Callers may inject ``openrouter_entries`` / ``upstream_entries`` (tests,
    offline runs). When None and not skipped, each source is fetched live;
    a failed live source is treated as empty so one outage never wipes the
    other sources. Overrides always load from disk. Returns the file path.
    """
    layers: list[dict[str, dict]] = [load_overrides_entries(config)]

    if openrouter_entries is None and not skip_openrouter:
        try:
            openrouter_entries = fetch_openrouter_prices()
        except RuntimeError:
            openrouter_entries = {}
    if upstream_entries is None and not skip_upstream:
        try:
            url = source_url or config.litellm_cost_map_url
            upstream_entries = fetch_upstream_cost_entries(url)
        except RuntimeError:
            upstream_entries = {}
    layers.append(openrouter_entries or {})
    layers.append(upstream_entries or {})

    merged = merge_entries(*layers)
    doc = {
        "meta": {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": ["override", "openrouter", "litellm-upstream"],
            "total_entries": len(merged),
        },
        "prices": merged,
    }
    path: Path = get_prices_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return path
