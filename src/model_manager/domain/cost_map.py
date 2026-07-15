"""Domain logic for LiteLLM cost and context map management."""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict

from model_manager.config import AppConfig, get_litellm_cost_overrides_path, get_litellm_cost_map_output_path

def fetch_upstream_cost_map(url: str) -> Dict[str, Any]:
    """Download the latest cost map from the upstream URL."""
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to reach upstream cost map URL: {e}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Upstream cost map is not valid JSON: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error fetching cost map: {e}")

def load_overrides(config: AppConfig) -> Dict[str, Any]:
    """Load the local overrides from the data directory."""
    path = get_litellm_cost_overrides_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

def merge_cost_maps(upstream: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge upstream cost map with local overrides.

    Local overrides win. If a model exists in both, the local override's
    fields are merged into the upstream block to preserve any fields
    not specifically overridden.
    """
    # Deep copy of upstream to avoid mutating original
    merged = json.loads(json.dumps(upstream))

    # Handle metadata separately if it exists in overrides
    if "_meta" in overrides:
        merged["_meta"] = overrides["_meta"]
        # Remove _meta from the remaining overrides to avoid treating it as a model
        overrides = {k: v for k, v in overrides.items() if k != "_meta"}

    for model_id, override_data in overrides.items():
        if not isinstance(override_data, dict):
            continue

        if model_id in merged:
            # Merge fields into the existing model block
            merged[model_id].update(override_data)
        else:
            # Add new model block
            merged[model_id] = override_data

    return merged

def build_local_cost_map(config: AppConfig, source_url: str | None = None) -> Path:
    """
    Orchestrate the cost map build: Fetch -> Load Overrides -> Merge -> Write.
    """
    url = source_url or config.litellm_cost_map_url

    # 1. Fetch upstream
    upstream = fetch_upstream_cost_map(url)

    # 2. Load local overrides
    overrides = load_overrides(config)

    # 3. Merge
    merged = merge_cost_maps(upstream, overrides)

    # 4. Write to service directory
    output_path = get_litellm_cost_map_output_path(config)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(merged, indent=2))
    except PermissionError:
        raise RuntimeError(f"Permission denied when writing to {output_path}. Try running as root or check directory permissions.")
    except Exception as e:
        raise RuntimeError(f"Failed to save merged cost map to {output_path}: {e}")

    return output_path
