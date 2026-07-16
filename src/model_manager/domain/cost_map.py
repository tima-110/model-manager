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

    Local overrides are inserted right after the 'sample_spec' entry (or at
    the top if 'sample_spec' is absent) so they appear near the top of the
    output file for easy review, while preserving upstream ordering.

    Local overrides win. If a model exists in both, the local override's
    fields are merged into the upstream block to preserve any fields
    not specifically overridden.
    """
    # Deep copy of upstream to avoid mutating original
    merged_upstream = json.loads(json.dumps(upstream))

    # Merge overrides into upstream (local wins)
    for model_id, override_data in overrides.items():
        if not isinstance(override_data, dict):
            continue
        if model_id in merged_upstream:
            merged_upstream[model_id].update(override_data)
        else:
            merged_upstream[model_id] = override_data

    # Reorder: keep sample_spec first (if present), then overrides (with merged values), then rest of upstream
    final: Dict[str, Any] = {}

    # 1. Preserve sample_spec at the very top if it exists
    if "sample_spec" in merged_upstream:
        final["sample_spec"] = merged_upstream["sample_spec"]

    # 2. Insert override keys right after sample_spec (in their original order from the stub)
    for key in overrides.keys():
        if key != "sample_spec" and key in merged_upstream:
            final[key] = merged_upstream[key]

    # 3. Append remaining upstream entries (in their original order)
    for key, value in merged_upstream.items():
        if key not in final:
            final[key] = value

    return final

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