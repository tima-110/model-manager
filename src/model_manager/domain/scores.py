"""Logic for Artificial Analysis API ingestion and score lookup."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from model_manager.config import AppConfig, get_raw_scores_path, get_scores_path

def get_api_key() -> str | None:
    """Load AA API key from environment variables."""
    return os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY")

def fetch_aa_data(api_key: str, config: AppConfig) -> dict | None:
    """Fetch model data from Artificial Analysis API and save raw response."""
    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    headers = {"x-api-key": api_key}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

            raw_path = get_raw_scores_path(config)
            raw_path.write_text(json.dumps(data, indent=2))

            return data
    except Exception as e:
        print(f"Error fetching AA data: {e}", file=sys.stderr)
        return None

def process_aa_data(aa_response: dict, config: AppConfig) -> dict | None:
    """Transform API response into a slug-keyed dictionary of scores."""
    if not aa_response or "data" not in aa_response:
        return None

    models_data = aa_response["data"]
    processed = {
        "meta": {
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Artificial Analysis API v2",
            "total_models": len(models_data)
        },
        "models": {}
    }

    for m in models_data:
        slug = m.get("slug")
        if not slug:
            continue

        evals = m.get("evaluations", {})
        processed["models"][slug] = {
            "name": m.get("name"),
            "scores": {
                "intelligence": evals.get("artificial_analysis_intelligence_index"),
                "coding": evals.get("artificial_analysis_coding_index"),
                "math": evals.get("artificial_analysis_math_index"),
            },
            "last_synced": processed["meta"]["last_updated"]
        }

    scores_path = get_scores_path(config)
    scores_path.write_text(json.dumps(processed, indent=2))
    return processed
