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

AA_LANGUAGE_MODELS_FREE_URL = "https://artificialanalysis.ai/api/v2/language/models/free"


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

def fetch_agentic_index_from_api(api_key: str, max_pages: int = 20) -> dict[str, float]:
    """Fetch {slug: agentic_index} from the AA language-models endpoint.

    The ``artificial_analysis_agentic_index`` composite is part of the free
    response's evaluations object. Results are paginated (200/page), so all
    pages are walked. Slugs without a value are omitted. On any failure
    returns whatever was collected so far (possibly ``{}``) so callers can
    fall back gracefully.
    """
    indexes: dict[str, float] = {}
    page = 1
    while page <= max_pages:
        try:
            req = urllib.request.Request(
                f"{AA_LANGUAGE_MODELS_FREE_URL}?page={page}",
                headers={"x-api-key": api_key},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode())
        except Exception as e:
            print(f"Error fetching AA agentic index (page {page}): {e}", file=sys.stderr)
            break
        for m in payload.get("data", []):
            value = (m.get("evaluations") or {}).get("artificial_analysis_agentic_index")
            if value is not None and m.get("slug"):
                indexes[m["slug"]] = value
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        page += 1
    return indexes

def process_aa_data(
    aa_response: dict,
    config: AppConfig,
    agentic_indexes: dict[str, float] | None = None,
) -> dict | None:
    """Transform API response into a slug-keyed dictionary of scores.

    ``agentic_indexes`` maps AA slugs to Agentic Index scores from the API;
    omitted, agentic scores are left as ``None``; call
    ``merge_agentic_scores`` afterwards to populate them.
    """
    if not aa_response or "data" not in aa_response:
        return None

    if agentic_indexes is None:
        agentic_indexes = {}

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
                "agentic": agentic_indexes.get(slug),
                "ttft": m.get("median_time_to_first_token_seconds"),
                "tps": m.get("median_output_tokens_per_second"),
            },
            "last_synced": processed["meta"]["last_updated"]
        }

    scores_path = get_scores_path(config)
    scores_path.write_text(json.dumps(processed, indent=2))
    return processed

def merge_agentic_scores(config: AppConfig, api_key: str) -> int:
    """Fetch AA agentic scores from the API and merge them into the saved scores JSON.

    Only updates models that already exist in the processed scores file,
    and only when the API provides a value, so a failed/partial fetch never
    wipes previously saved agentic data.
    Returns the number of models whose agentic score was set or changed.
    """
    scores_path = get_scores_path(config)
    if not scores_path.exists():
        raise RuntimeError("Processed scores file not found. Please run 'scores fetch' first.")

    indexes = fetch_agentic_index_from_api(api_key)
    if not indexes:
        return 0

    try:
        data = json.loads(scores_path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError("Processed scores file is corrupted.")

    updated = 0
    for slug, entry in data.get("models", {}).items():
        fetched = indexes.get(slug)
        if fetched is None:
            continue
        entry.setdefault("scores", {})
        fetched = round(fetched, 1)
        if entry["scores"].get("agentic") != fetched:
            entry["scores"]["agentic"] = fetched
            updated += 1

    if updated:
        scores_path.write_text(json.dumps(data, indent=2))
    return updated

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
                new_scores = dict(processed_models[slug].get("scores") or {})
                # Preserve the existing agentic score when the new value is
                # unavailable (API gap), so a failed/partial fetch never
                # wipes previously synced agentic data.
                existing_agentic = (variant_info.get("scores") or {}).get("agentic")
                if new_scores.get("agentic") is None and existing_agentic is not None:
                    new_scores["agentic"] = existing_agentic
                variant_info["scores"] = new_scores
                updated_count += 1

    storage.save_models_data(config, models_data)
    return updated_count
