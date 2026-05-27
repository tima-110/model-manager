"""OpenRouter model discovery logic."""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from model_manager.config import AppConfig

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

def fetch_free_models() -> List[Dict[str, Any]]:
    """Fetch all models from OpenRouter and filter for free ones."""
    try:
        req = urllib.request.Request(OPENROUTER_API_URL)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            all_models = data.get("data", [])

            free_models = []
            for m in all_models:
                pricing = m.get("pricing", {})
                # A model is free if both prompt and completion costs are 0
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
        # We'll let the CLI handle the error reporting
        raise RuntimeError(f"Failed to fetch models from OpenRouter: {e}")

def probe_model(model_id: str, api_key: Optional[str]) -> bool:
    """Check if a model is responsive by sending a minimal request."""
    if not api_key:
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/castor-claw/model-manager",
        "X-Title": "Model Manager Discovery"
    }

    data = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1
    }

    try:
        req = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=json.dumps(data).encode(),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            # Check for 200 OK
            if response.status == 200:
                body = json.loads(response.read().decode())
                # Some models return 200 but embed an error in the JSON
                if "error" in body:
                    return False
                return True
            return False
    except Exception:
        return False

def save_free_models(config: AppConfig, models: List[Dict[str, Any]], path: Path) -> None:
    """Save the list of free models to a JSON file."""
    with open(path, "w") as f:
        json.dump({
            "count": len(models),
            "models": models
        }, f, indent=2)
