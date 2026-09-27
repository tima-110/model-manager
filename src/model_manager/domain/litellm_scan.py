"""Headless scan of models served by the local LiteLLM proxy.

Enumerates targets from the generated YAML configs (desired state) plus
every alias in the merged router_settings file, optionally reconciles
against the proxy's live model list (drift report), then probes each
target sequentially through the OpenAI-compatible chat endpoint with a
fixed-size streamed completion while measuring TTFT and throughput.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from model_manager.config import AppConfig
from model_manager.domain import auth

DEFAULT_BASE_URL = "http://localhost:4000"
DEFAULT_TIMEOUT_SEC = 180
DEFAULT_MAX_TOKENS = 64
LITELLM_API_KEY_NAME = "LITELLM_MASTER_KEY"
FIXED_PROMPT = "List the numbers 1 through 20, one per line, nothing else."


def _load_yaml(path: Path) -> dict:
    """Load a YAML mapping; raise RuntimeError on problems."""
    if not path.exists():
        raise RuntimeError(f"YAML file not found: {path}")
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(doc, dict):
        raise RuntimeError(f"Expected a YAML mapping in {path}")
    return doc


def enumerate_targets(config: AppConfig) -> dict[str, list[str]]:
    """Collect scan targets from local configs (no proxy needed).

    Returns {"models": [...model_names...], "aliases": [...alias names...]}.
    Model names come from every file in the litellm.yaml ``include:`` list;
    aliases come from the merged router_settings file (stub hand aliases +
    generated tiers), falling back to the standalone aliases file.
    """
    models: list[str] = []
    litellm_cfg = _load_yaml(config.litellm_config_path)
    base_dir = config.litellm_config_path.parent
    for included in litellm_cfg.get("include") or []:
        try:
            doc = _load_yaml(base_dir / included)
        except RuntimeError:
            continue
        for entry in doc.get("model_list") or []:
            name = entry.get("model_name")
            if name and name not in models:
                models.append(name)

    aliases: list[str] = []
    for candidate in (config.litellm_router_settings_path, config.litellm_aliases_path):
        try:
            doc = _load_yaml(candidate)
        except RuntimeError:
            continue
        if candidate == config.litellm_router_settings_path:
            alias_map = (doc.get("router_settings") or {}).get("model_group_alias") or {}
        else:
            alias_map = doc.get("model_group_alias") or {}
        for alias in alias_map:
            if alias not in aliases:
                aliases.append(alias)
        if aliases:
            break

    return {"models": models, "aliases": aliases}


def fetch_proxy_models(base_url: str, api_key: str, timeout: int = 30) -> list[str]:
    """Return model IDs reported by the live proxy (GET /v1/models)."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except Exception as e:
        raise RuntimeError(f"Could not list proxy models: {e}") from e
    return [m["id"] for m in payload.get("data", []) if m.get("id")]


def reconcile_targets(targets: dict[str, list[str]], served: list[str]) -> dict[str, Any]:
    """Compare desired targets against the live proxy list (drift report)."""
    served_set = set(served)
    all_targets = targets["models"] + targets["aliases"]
    unserved = [t for t in all_targets if t not in served_set]
    extra = [s for s in served if s not in all_targets]
    return {
        "served_count": len(served),
        "target_count": len(all_targets),
        "unserved": unserved,
        "extra_served": extra,
    }


def probe_litellm_model(
    base_url: str,
    api_key: str,
    model: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Probe one model through the proxy with a fixed-size streamed completion.

    Returns a record with status, code, TTFT/throughput metrics, token
    counts, and error text. Never raises for HTTP/transport outcomes
    (missing key raises RuntimeError instead).
    """
    if not api_key:
        raise RuntimeError(f"API key {LITELLM_API_KEY_NAME} missing from keychain.")

    body = {
        "model": model,
        "messages": [{"role": "user", "content": FIXED_PROMPT}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record: dict[str, Any] = {
        "model": model,
        "timestamp": timestamp,
        "requested_tokens": max_tokens,
        "status": "down",
        "code": None,
        "ttft_ms": None,
        "total_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "tps": None,
        "tpm_est": None,
        "error": None,
    }

    t0 = time.perf_counter()
    first_chunk_at: float | None = None
    usage: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            record["code"] = str(response.getcode())
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices and (choices[0].get("delta") or {}).get("content"):
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter()
                if chunk.get("usage"):
                    usage = chunk["usage"]
        total_ms = (time.perf_counter() - t0) * 1000
        record["total_ms"] = round(total_ms, 1)
        if first_chunk_at is None:
            record["status"] = "down"
            record["error"] = "no content chunks received"
            return record
        record["ttft_ms"] = round((first_chunk_at - t0) * 1000, 1)
        record["prompt_tokens"] = usage.get("prompt_tokens")
        record["completion_tokens"] = usage.get("completion_tokens")
        gen_sec = (time.perf_counter() - first_chunk_at) or 1e-9
        if record["completion_tokens"]:
            record["tps"] = round(record["completion_tokens"] / gen_sec, 2)
            record["tpm_est"] = round(record["tps"] * 60, 1)
        record["status"] = "up"
    except urllib.error.HTTPError as e:
        record["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        record["code"] = str(e.code)
        try:
            record["error"] = e.read().decode(errors="replace")[:500]
        except Exception:
            record["error"] = str(e.reason)
        if e.code in (401, 403):
            record["status"] = "unauthorized"
        elif e.code == 404:
            record["status"] = "not_found"
        elif e.code == 429:
            record["status"] = "ratelimit"
        else:
            record["status"] = "down"
    except (urllib.error.URLError, TimeoutError) as e:
        record["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        record["code"] = "000"
        record["status"] = "timeout" if "timed out" in str(e).lower() else "down"
        record["error"] = str(e.reason) if hasattr(e, "reason") else str(e)
    except Exception as e:
        record["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        record["code"] = "ERR"
        record["error"] = str(e)
    return record


def scan_targets(
    base_url: str,
    api_key: str,
    targets: list[str],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    on_result: Any = None,
) -> list[dict[str, Any]]:
    """Probe each target sequentially; optional per-result hook."""
    records: list[dict[str, Any]] = []
    for target in targets:
        record = probe_litellm_model(
            base_url, api_key, target, max_tokens=max_tokens, timeout=timeout
        )
        records.append(record)
        if on_result is not None:
            on_result(record)
    return records


def save_litellm_scan(config: AppConfig, records: list[dict[str, Any]]) -> Path:
    """Write scan records plus summary to data_dir/litellm_scan.json."""
    up = sum(1 for r in records if r["status"] == "up")
    ttfts = [r["ttft_ms"] for r in records if r["ttft_ms"] is not None]
    tps_vals = [r["tps"] for r in records if r["tps"] is not None]
    doc = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scanned": len(records),
            "up": up,
            "avg_ttft_ms": round(sum(ttfts) / len(ttfts), 1) if ttfts else None,
            "avg_tps": round(sum(tps_vals) / len(tps_vals), 2) if tps_vals else None,
        },
        "models": records,
    }
    path = config.data_dir / "litellm_scan.json"
    path.write_text(json.dumps(doc, indent=2))
    return path
