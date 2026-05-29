"""OpenRouter, NVIDIA, and Ollama model discovery logic."""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, NamedTuple
from dataclasses import dataclass

from model_manager.config import AppConfig

# --- Constants ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OLLAMA_API_URL = "https://ollama.com/api/tags"
OLLAMA_CHAT_URL = "https://ollama.com/api/chat"

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

def fetch_nvidia_models(api_key: str) -> List[Dict[str, Any]]:
    """Fetch models from NVIDIA and filter for free tier ones."""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(NVIDIA_API_URL, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("data", [])

            free_models = []
            for m in all_models:
                model_id = m.get("id", "")
                free_models.append({
                    "id": model_id,
                    "name": m.get("id"),
                    "context_length": None,
                    "architecture": None,
                    "description": None,
                    "tags": [],
                })
            return free_models
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from NVIDIA: {e}")

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

def probe_model(model_id: str, api_key: Optional[str], provider: str = "openrouter") -> PingResult:
    """Check if a model is responsive and measure TTFB latency."""
    if not api_key:
        return PingResult(status="unauthorized", latency_ms=0, code="401", error="API key missing")

    if provider == "ollama":
        chat_url = OLLAMA_CHAT_URL
    elif provider == "nvidia":
        chat_url = NVIDIA_CHAT_URL
    else:
        chat_url = OPENROUTER_CHAT_URL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/castor-claw/model-manager"
        headers["X-Title"] = "Model Manager Discovery"

    data = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1
    }

    if provider == "ollama":
        data["stream"] = False

    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            chat_url,
            data=json.dumps(data).encode(),
            headers=headers,
            method="POST"
        )
        # We only care about TTFB (Time To First Byte), so we use a short timeout
        # and read only a small amount of the response.
        with urllib.request.urlopen(req, timeout=10) as response:
            latency = (time.perf_counter() - t0) * 1000
            code = str(response.getcode())
            status = STATUS_MAP.get(code, "down")
            return PingResult(status=status, latency_ms=latency, code=code)
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - t0) * 1000
        code = str(e.code)
        status = STATUS_MAP.get(code, "down")
        return PingResult(status=status, latency_ms=latency, code=code, error=e.reason)
    except urllib.error.URLError as e:
        latency = (time.perf_counter() - t0) * 1000
        return PingResult(status="down", latency_ms=latency, code="000", error=str(e.reason))
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return PingResult(status="down", latency_ms=latency, code="ERR", error=str(e))

def scan_models(provider_id: str, api_key: str, model_ids: List[str], concurrency: int = 10) -> Dict[str, PingResult]:
    """Ping a list of models in parallel and return results."""
    results = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_model = {
            executor.submit(probe_model, mid, api_key, provider_id): mid
            for mid in model_ids
        }
        for future in __import__("concurrent.futures").as_completed(future_to_model):
            mid = future_to_model[future]
            try:
                results[mid] = future.result()
            except Exception as e:
                results[mid] = PingResult(status="down", latency_ms=0, code="ERR", error=str(e))
    return results

def save_free_models(config: AppConfig, models: List[Dict[str, Any]], path: Path) -> None:
    """Save the list of free models to a JSON file."""
    with open(path, "w") as f:
        json.dump({
            "count": len(models),
            "models": models
        }, f, indent=2)

def save_scan_results(config: AppConfig, provider: str, results: Dict[str, Any]) -> None:
    """Save the scan history and summaries to a JSON file."""
    path = config.data_dir / f"{provider.lower()}_scan.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
