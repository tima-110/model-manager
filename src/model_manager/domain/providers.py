"""Domain logic for managing supported providers."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from model_manager.config import (
    AppConfig,
    get_free_models_path,
    get_nvidia_models_path,
    get_ollama_models_path,
    get_gemini_models_path,
    get_huggingface_models_path,
)
from model_manager.domain import auth, discovery, storage

@dataclass
class Provider:
    """Represents a supported model provider and its discovery configuration."""
    name: str
    secret_key: str
    fetch_fn: Callable[..., List[Dict[str, Any]]]
    path_fn: Callable[[AppConfig], Path]
    probe_id: str
    scan_concurrency: int = 10
    scan_delay_between_models_ms: int = 0
    cycle_delay_sec: int | None = None

# The current list of supported providers.
SUPPORTED_PROVIDERS = [
    Provider(
        name="OpenRouter",
        secret_key="OPENROUTER_API_KEY",
        fetch_fn=discovery.fetch_openrouter_free_models,
        path_fn=get_free_models_path,
        probe_id="openrouter",
    ),
    Provider(
        name="NVIDIA",
        secret_key="NVIDIA_API_KEY",
        fetch_fn=discovery.fetch_nvidia_models,
        path_fn=get_nvidia_models_path,
        probe_id="nvidia",
    ),
    Provider(
        name="Ollama",
        secret_key="OLLAMA_API_KEY",
        fetch_fn=discovery.fetch_ollama_models,
        path_fn=get_ollama_models_path,
        probe_id="ollama",
    ),
    Provider(
        name="Gemini",
        secret_key="GEMINI_API_KEY",
        fetch_fn=discovery.fetch_gemini_models,
        path_fn=get_gemini_models_path,
        probe_id="gemini",
        scan_concurrency=1,
        scan_delay_between_models_ms=600,
    ),
    Provider(
        name="HuggingFace",
        secret_key="HF_TOKEN",
        fetch_fn=discovery.fetch_huggingface_models,
        path_fn=get_huggingface_models_path,
        probe_id="huggingface",
        scan_concurrency=1,
        scan_delay_between_models_ms=600,
    ),
]

def list_providers() -> List[Provider]:
    """Return the list of supported providers."""
    return SUPPORTED_PROVIDERS

def run_discovery_workflow(provider: Provider, config: AppConfig, probe: bool = False) -> List[Dict[str, Any]]:
    """
    Orchestrate the discovery process for a provider:
    Fetch -> Optional Probe -> Save.

    Returns the final list of discovered models.
    """
    # 1. Secret Retrieval
    api_key = auth.get_secret(provider.secret_key)

    # OpenRouter public endpoint doesn't strictly require a key,
    # but NVIDIA and Ollama do.
    if provider.name != "OpenRouter" and not api_key:
        raise RuntimeError(f"API key {provider.secret_key} missing from keychain.")

    # 2. Fetch
    # Handle fetch functions that require API key vs those that don't
    if provider.name == "OpenRouter":
        models = provider.fetch_fn()
    else:
        models = provider.fetch_fn(api_key)

    # 3. Optional Probe
    if probe:
        verified_models = []
        for m in models:
            if discovery.probe_model(m["id"], api_key, provider=provider.probe_id):
                verified_models.append(m)
        models = verified_models

    # 4. Save
    path = provider.path_fn(config)
    discovery.save_free_models(config, models, path)

    return models


def assess_scan_results(results: List["discovery.PingResult"]) -> str:
    """Return the health assessment label for a model's ping history.

    Excludes 'unsupported' results from availability calculation
    since those models can't be probed via this scan method.
    """
    if not results:
        return "Unknown"

    relevant = [r for r in results if r.status != "unsupported"]
    if not relevant:
        return "Unsupported"

    successes = [r for r in relevant if r.status == "up"]
    avail = len(successes) / len(relevant)

    if avail > 0.9:
        avg_lat = sum(r.latency_ms for r in successes) / len(successes)
        if avg_lat < 1000:
            return "Good"
        return "Slow"

    counts: Dict[str, int] = {}
    for r in relevant:
        counts[r.status] = counts.get(r.status, 0) + 1
    dominant = max(counts, key=counts.get)

    if dominant in ("unauthorized", "forbidden"):
        return "Unauthorized"
    if dominant == "not_found":
        return "Not Found"
    if dominant == "ratelimit":
        return "Ratelimited"
    if dominant in ("down", "timeout"):
        return "Dead"
    return "Weak"


def scan_provider_models(
    provider: Provider,
    config: AppConfig,
    *,
    max_scans: int | None = None,
    filter_str: str | None = None,
    only_up: bool = False,
    only_down: bool = False,
    debug: bool = False,
    on_cycle: Optional[Callable[[int, Dict[str, "discovery.PingResult"], Dict[str, List["discovery.PingResult"]]], None]] = None,
) -> Dict[str, Any]:
    """Headless health-scan workflow for a provider's cached models.

    Reads model IDs from the provider cache (populated by fetch), pings
    them for up to ``max_scans`` cycles (defaults to ``config.scan_count``),
    writes per-model history plus summary into ``models.json`` and
    ``<provider>_scan.json``.

    ``on_cycle`` is an optional hook invoked after each cycle with
    ``(cycle_number, cycle_results, history)`` for live display. Callers
    that cannot display progress pass nothing.

    Returns a summary dict with provider, cycles, scanned count,
    per-model assessments, and the debug log path (if any).

    Raises:
        RuntimeError: If the API key (except OpenRouter) or provider
            cache is missing.
    """
    api_key = auth.get_secret(provider.secret_key)
    if not api_key and provider.name != "OpenRouter":
        raise RuntimeError(f"API key {provider.secret_key} missing from keychain.")

    cache_path = provider.path_fn(config)
    if not cache_path.exists():
        raise RuntimeError(f"Provider cache not found at {cache_path}. Please run 'fetch' first.")

    with open(cache_path, "r") as f:
        cache_data = json.load(f)
        all_models = cache_data.get("models", [])

    if filter_str:
        model_ids = [
            m["id"] for m in all_models
            if filter_str.lower() in m["id"].lower() or filter_str.lower() in m.get("name", "").lower()
        ]
    else:
        model_ids = [m["id"] for m in all_models]

    if not model_ids:
        return {"provider": provider.name, "cycles": 0, "scanned": 0, "assessments": {}, "debug_file": None}

    def _tunable(field: str) -> Any:
        # Provider config may be a ProviderConfig or a plain dict when the
        # section is absent; either way, fall back to provider defaults.
        section = config.providers.get(provider.name.lower(), {})
        if isinstance(section, dict):
            return section.get(field)
        return getattr(section, field, None)

    scan_concurrency = _tunable("scan_concurrency")
    if scan_concurrency is None:
        scan_concurrency = provider.scan_concurrency
    scan_delay = _tunable("scan_delay_between_models_ms")
    if scan_delay is None:
        scan_delay = provider.scan_delay_between_models_ms
    cycle_delay = _tunable("cycle_delay_sec")
    if cycle_delay is None:
        cycle_delay = provider.cycle_delay_sec if provider.cycle_delay_sec is not None else config.scan_frequency

    max_cycles = max_scans if max_scans is not None else config.scan_count

    history: Dict[str, List["discovery.PingResult"]] = {mid: [] for mid in model_ids}
    debug_logs: List[Dict[str, Any]] = []
    cycle_count = 0

    while True:
        cycle_count += 1
        results = discovery.scan_models(
            provider.probe_id, api_key or "", model_ids,
            concurrency=scan_concurrency, delay_between_models_ms=scan_delay, debug=debug,
        )

        if debug:
            for mid, res in results.items():
                if res and res.debug_info:
                    debug_logs.append({"cycle": cycle_count, "model_id": mid, "debug": res.debug_info})

        for mid in model_ids:
            res = results.get(mid)
            if res:
                if only_up and res.status != "up":
                    continue
                if only_down and res.status == "up":
                    continue
                history[mid].append(res)

        if on_cycle is not None:
            on_cycle(cycle_count, results, history)

        if max_cycles > 0 and cycle_count >= max_cycles:
            break
        time.sleep(cycle_delay)

    timestamp = datetime.now(timezone.utc).isoformat()
    final_results_data: Dict[str, Any] = {
        "metadata": {"provider": provider.name, "cycles": cycle_count, "timestamp": timestamp},
        "models": {},
    }
    assessments: Dict[str, str] = {}

    for mid in model_ids:
        m_hist = history[mid]
        relevant = [r for r in m_hist if r.status != "unsupported"]
        successes = [r for r in relevant if r.status == "up"]
        avail = len(successes) / len(relevant) if relevant else 0
        avg_lat = sum(r.latency_ms for r in successes) / len(successes) if successes else 0
        label = assess_scan_results(m_hist)
        assessments[mid] = label
        final_results_data["models"][mid] = {
            "history": [vars(r) for r in m_hist],
            "summary": {"availability": avail, "avg_latency": avg_lat, "assessment": label},
        }

    debug_file: str | None = None
    if debug and debug_logs:
        debug_data = {
            "metadata": {"provider": provider.name, "cycles": cycle_count, "timestamp": timestamp},
            "logs": debug_logs,
        }
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        debug_path = config.data_dir / f"debug_scan_{provider.name.lower()}_{ts}.json"
        debug_path.write_text(json.dumps(debug_data, indent=2))
        debug_file = str(debug_path)

    models_data = storage.load_models_data(config)
    updated = False
    for mid, scan_data in final_results_data["models"].items():
        summary = scan_data["summary"]
        for model_id, model_info in models_data.get("models", {}).items():
            for variant_id, variant_info in model_info.get("variants", {}).items():
                for prov, pids in variant_info.get("provider_ids", {}).items():
                    if prov.lower() == provider.name.lower():
                        if isinstance(pids, dict) and mid in pids:
                            pids[mid].update({
                                "availability": summary["availability"],
                                "avg_latency": summary["avg_latency"],
                                "assessment": summary["assessment"],
                                "scan_timestamp": timestamp,
                            })
                            updated = True
    if updated:
        storage.save_models_data(config, models_data)

    discovery.save_scan_results(config, provider.name, final_results_data)

    return {
        "provider": provider.name,
        "cycles": cycle_count,
        "scanned": len(model_ids),
        "assessments": assessments,
        "results": final_results_data,
        "debug_file": debug_file,
        "models_updated": updated,
    }
