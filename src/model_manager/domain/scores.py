"""Logic for Artificial Analysis API ingestion and score lookup."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from model_manager.config import AppConfig, get_raw_scores_path, get_scores_path
from model_manager.domain import auth, storage


def get_api_key() -> str | None:
    """Load AA API key from environment or keychain."""
    return auth.get_secret("ARTIFICIAL_ANALYSIS_API_KEY")

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
                "ttft": m.get("median_time_to_first_token_seconds"),
                "tps": m.get("median_output_tokens_per_second"),
            },
            "last_synced": processed["meta"]["last_updated"]
        }

    scores_path = get_scores_path(config)
    scores_path.write_text(json.dumps(processed, indent=2))
    return processed

def get_scores_for_slug(config: AppConfig, slug: str) -> dict | None:
    """Retrieve scores for a specific AA slug from the processed scores file."""
    path = get_scores_path(config)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        model_data = data.get("models", {}).get(slug)
        if model_data:
            return model_data.get("scores")
    except Exception:
        pass
    return None


def list_all_scores(config: AppConfig) -> dict:
    """Return all processed scores from the local cache."""
    path = get_scores_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data.get("models", {})
    except Exception:
        return {}



def sync_scores_to_models(config: AppConfig) -> int:
    """
    Update the scores in models.json based on the current values in scores.json.
    Only updates variants that have an aa_slug.
    """
    scores_path = get_scores_path(config)
    if not scores_path.exists():
        raise RuntimeError("Processed scores file not found. Please run 'scores fetch' first.")

    try:
        scores_data = json.loads(scores_path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError("Processed scores file is corrupted.")

    processed_models = scores_data.get("models", {})
    models_data = storage.load_models_data(config)
    updated_count = 0

    for model_id, model_info in models_data.get("models", {}).items():
        for variant_id, variant_info in model_info.get("variants", {}).items():
            slug = variant_info.get("aa_slug")
            if slug and slug in processed_models:
                # Update the scores with current values from scores.json
                variant_info["scores"] = processed_models[slug].get("scores")
                updated_count += 1

    storage.save_models_data(config, models_data)
    return updated_count
