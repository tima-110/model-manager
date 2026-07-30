"""OpenRouter, NVIDIA, and Ollama model discovery logic."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, NamedTuple
from dataclasses import dataclass

from model_manager.config import AppConfig

# --- Constants ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_NVCF_API_URL = "https://api.nvcf.nvidia.com/v2/nvcf/functions"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OLLAMA_API_URL = "https://ollama.com/api/tags"
OLLAMA_CHAT_URL = "https://ollama.com/api/chat"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_CHAT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OLLAMA_API_URL = "https://ollama.com/api/tags"
OLLAMA_CHAT_URL = "https://ollama.com/api/chat"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_CHAT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

STATUS_MAP = {
    "200": "up",
    "401": "unauthorized",
    "403": "forbidden",
    "404": "not_found",
    "408": "timeout",
    "429": "ratelimit",
    "000": "timeout",
    "500": "down",
    "502": "down",
    "503": "unavailable",
    "504": "down",
}

@dataclass
class PingResult:
    """The result of a single model health probe."""
    status: str
    latency_ms: float
    code: str
    error: Optional[str] = None
    debug_info: Optional[Dict[str, Any]] = None

def fetch_openrouter_free_models() -> List[Dict[str, Any]]:
    """Fetch all models from OpenRouter and filter for free ones."""
    try:
        req = urllib.request.Request(OPENROUTER_API_URL)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("data", [])

            free_models = []
            for m in all_models:
                pricing = m.get("pricing", {})
                if (float(pricing.get("prompt", -1)) == 0 and
                    float(pricing.get("completion", -1)) == 0):

                    free_models.append({
                        "id": m.get("id"),
                        "name": m.get("name"),
                        "context_length": m.get("context_length"),
                        "architecture": m.get("architecture"),
                        "description": m.get("description"),
                        "tags": m.get("tags", []),
                    })
            return free_models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from OpenRouter: {e}")

def _parse_model_id(model_id: str) -> dict:
    """Extract owner, short name, and param count from a model ID.

    NVIDIA v1 model IDs follow the pattern {owner}/{model-family}-{size}b-{suffix}.
    Example: meta/llama-3.1-8b-instruct -> owner=meta, name=llama-3.1-8b-instruct, size=8
    """
    parts = model_id.split("/")
    owner = parts[0] if len(parts) > 1 else "unknown"
    short_name = parts[-1]
    match = re.search(r'(\d+)b', short_name)
    param_count = int(match.group(1)) if match else None
    return {
        "owner": owner,
        "short_name": short_name,
        "param_count_b": param_count,
    }


def _normalize_v1_to_nvcf(model_id: str) -> str:
    """Normalize a v1 model ID to match the NVCF function naming convention.

    v1: meta/llama-3.1-8b-instruct -> NVCF: ai-llama-3_1-8b-instruct
    The owner prefix is stripped and dots are replaced with underscores.
    """
    short = model_id.split("/")[-1].lower()
    return short.replace(".", "_")


def fetch_nvidia_models(api_key: str, enrich_nvcf: bool = True) -> List[Dict[str, Any]]:
    """Fetch models from NVIDIA API.

    Fetches the base model list from /v1/models, parses model IDs for owner
    and param count, and optionally enriches with NVCF deployment status.

    Args:
        api_key: NVIDIA API key.
        enrich_nvcf: If True, also fetch from the NVCF functions API to get
            deployment status (ACTIVE/INACTIVE/DEGRADING) and health endpoint
            info for each model.

    Returns:
        List of model dicts with id, name, owner, param_count_b, and (when
        enriched) nvcf_status and health_uri.
    """
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(NVIDIA_API_URL, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("data", [])

        # Optionally fetch NVCF enrichment
        nvcf_lookup: dict[str, dict] = {}
        if enrich_nvcf:
            nvcf_lookup = _fetch_nvcf_enrichment(api_key)

        free_models = []
        for m in all_models:
            model_id = m.get("id", "")
            parsed = _parse_model_id(model_id)
            normalized = _normalize_v1_to_nvcf(model_id)
            nvcf = nvcf_lookup.get(normalized, {})

            free_models.append({
                "id": model_id,
                "name": model_id,
                "owner": parsed["owner"],
                "short_name": parsed["short_name"],
                "param_count_b": parsed["param_count_b"],
                "context_length": None,
                "architecture": None,
                "description": nvcf.get("description", ""),
                "tags": [],
                "nvcf_status": nvcf.get("status", "unknown"),
            })
        return free_models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from NVIDIA: {e}")


def _fetch_nvcf_enrichment(api_key: str) -> dict[str, dict]:
    """Fetch NVCF function statuses and build a lookup keyed by normalized name.

    Returns:
        Dict mapping normalized model names (e.g. 'llama-3_1-8b-instruct')
        to their NVCF data (status, health_uri, description, etc.).
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(NVIDIA_NVCF_API_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except Exception:
        return {}

    functions = body.get("functions", [])
    lookup: dict[str, dict] = {}
    for fn in functions:
        name = fn.get("name", "")
        # Strip "ai-" prefix to match normalized v1 names
        normalized = name.lower()
        if normalized.startswith("ai-"):
            normalized = normalized[3:]
        lookup[normalized] = {
            "status": fn.get("status", "unknown"),
            "health_uri": fn.get("healthUri", ""),
            "health_port": fn.get("health", {}).get("port"),
            "created_at": fn.get("createdAt", ""),
            "description": fn.get("description", ""),
        }

    return lookup

def fetch_ollama_models(api_key: str) -> List[Dict[str, Any]]:
    """Fetch models from Ollama Cloud and map to internal representation."""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(OLLAMA_API_URL, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("models", [])

            models = []
            for m in all_models:
                model_id = m.get("name", "")
                models.append({
                    "id": model_id,
                    "name": model_id,
                    "context_length": None,
                    "architecture": None,
                    "description": None,
                    "tags": [],
                })
            return models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from Ollama: {e}")

def fetch_gemini_models(api_key: str) -> List[Dict[str, Any]]:
    """Fetch models from Gemini (Google AI Studio)."""
    try:
        url = f"{GEMINI_API_URL}?key={api_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("models", [])

            models = []
            for m in all_models:
                # Only include models that support content generation
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    model_id = m.get("name", "").replace("models/", "")
                    models.append({
                        "id": model_id,
                        "name": m.get("displayName"),
                        "context_length": m.get("inputTokenLimit"),
                        "architecture": None,
                        "description": m.get("description"),
                        "tags": [],
                    })
            return models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from Gemini: {e}")

def probe_model(model_id: str, api_key: Optional[str], provider: str = "openrouter", debug: bool = False) -> PingResult:
    """Check if a model is responsive and measure TTFB latency."""
    if not api_key:
        return PingResult(status="unauthorized", latency_ms=0, code="401", error="API key missing")

    if provider == "ollama":
        chat_url = OLLAMA_CHAT_URL
    elif provider == "nvidia":
        chat_url = NVIDIA_CHAT_URL
    elif provider == "gemini":
        chat_url = f"{GEMINI_CHAT_URL_TEMPLATE.format(model=model_id)}?key={api_key}"
    else:
        chat_url = OPENROUTER_CHAT_URL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if provider == "gemini":
        # Gemini uses API key in URL, not Bearer token
        headers.pop("Authorization", None)

    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/castor-claw/model-manager"
        headers["X-Title"] = "Model Manager Discovery"

    if provider == "gemini":
        data = {
            "contents": [{"parts": [{"text": "hi"}]}]
        }
    else:
        data = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1
        }

    if provider == "ollama":
        data["stream"] = False

    t0 = time.perf_counter()
    debug_info = None
    try:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(
            chat_url,
            data=payload,
            headers=headers,
            method="POST"
        )
        # We only care about TTFB (Time To First Byte), so we use a short timeout
        # and read only a small amount of the response.
        with urllib.request.urlopen(req, timeout=10) as response:
            latency = (time.perf_counter() - t0) * 1000
            code = str(response.getcode())
            status = STATUS_MAP.get(code, "down")

            if debug:
                response_body = response.read().decode(errors="replace")
                debug_info = {
                    "request": {"url": chat_url, "headers": headers, "payload": data},
                    "response": {"code": code, "body": response_body}
                }

            return PingResult(status=status, latency_ms=latency, code=code, debug_info=debug_info)
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - t0) * 1000
        code = str(e.code)
        status = STATUS_MAP.get(code, "down")

        if debug:
            response_body = e.read().decode(errors="replace")
            debug_info = {
                "request": {"url": chat_url, "headers": headers, "payload": data},
                "response": {"code": code, "body": response_body}
            }

        return PingResult(status=status, latency_ms=latency, code=code, error=e.reason, debug_info=debug_info)
    except urllib.error.URLError as e:
        latency = (time.perf_counter() - t0) * 1000
        if debug:
            debug_info = {
                "request": {"url": chat_url, "headers": headers, "payload": data},
                "response": {"error": str(e.reason)}
            }
        return PingResult(status="down", latency_ms=latency, code="000", error=str(e.reason), debug_info=debug_info)
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        if debug:
            debug_info = {
                "request": {"url": chat_url, "headers": headers, "payload": data},
                "response": {"error": str(e)}
            }
        return PingResult(status="down", latency_ms=latency, code="ERR", error=str(e), debug_info=debug_info)

def scan_models(provider_id: str, api_key: str, model_ids: List[str], concurrency: int = 10, debug: bool = False) -> Dict[str, PingResult]:
    """Ping a list of models in parallel and return results."""
    results = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_model = {
            executor.submit(probe_model, mid, api_key, provider_id, debug): mid
            for mid in model_ids
        }
        for future in as_completed(future_to_model):
            mid = future_to_model[future]
            try:
                results[mid] = future.result()
            except Exception as e:
                results[mid] = PingResult(status="down", latency_ms=0, code="ERR", error=str(e))
    return results

def save_free_models(config: AppConfig, models: List[Dict[str, Any]], path: Path) -> None:
    """Save the list of free models to a JSON file."""
    from datetime import datetime
    with open(path, "w") as f:
        json.dump({
            "count": len(models),
            "fetched_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "models": models
        }, f, indent=2)

def save_scan_results(config: AppConfig, provider: str, results: Dict[str, Any]) -> None:
    """Save the scan history and summaries to a JSON file."""
    path = config.data_dir / f"{provider.lower()}_scan.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
