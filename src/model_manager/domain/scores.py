"""Logic for Artificial Analysis API ingestion and score lookup."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from model_manager.config import AppConfig, get_raw_scores_path, get_scores_path
from model_manager.domain import auth, storage

AGENTIC_INDEX_URL = "https://artificialanalysis.ai/models/capabilities/agentic"


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

def fetch_agentic_indexes() -> dict[str, float]:
    """Scrape the AA Agentic Index page for {slug: agentic_index}.

    The free API does not expose an ``artificial_analysis_agentic_index``
    field yet, so we parse the Next.js flight/RSC payload embedded in the
    page. Returns a slug-keyed dict of agentic scores; on any failure
    returns ``{}`` so callers can fall back gracefully.
    """
    try:
        req = urllib.request.Request(AGENTIC_INDEX_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode()
    except Exception as e:
        print(f"Error fetching AA agentic index: {e}", file=sys.stderr)
        return {}

    rows = re.findall(r'self\.__next_f\.push\(\[1,"(.*)"\]\)', html)
    blob = "".join(rows)

    starts = list(re.finditer(r'\\"id\\":\\"[0-9a-f]{8}-[0-9a-f-]{27,36}\\",\\"slug\\":\\"', blob))
    indexes: dict[str, float] = {}
    for i, start in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(blob)
        segment = blob[start.start():end]
        slug = re.search(r'"slug\\":\\"([^\\]+)\\"', segment)
        headline = re.findall(r'"headlineValue\\":([0-9.]+|null)', segment)
        if slug and headline and headline[-1] != "null":
            try:
                indexes[slug.group(1)] = float(headline[-1])
            except ValueError:
                continue
    return indexes

def process_aa_data(
    aa_response: dict,
    config: AppConfig,
    agentic_indexes: dict[str, float] | None = None,
) -> dict | None:
    """Transform API response into a slug-keyed dictionary of scores.

    ``agentic_indexes`` maps AA slugs to Agentic Index scores scraped from
    the AA website (the free API does not expose the field yet). When
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

def merge_agentic_scores(config: AppConfig) -> int:
    """Scrape AA agentic scores and merge them into the saved scores JSON.

    Only updates models that already exist in the processed scores file,
    so the API fetch result is preserved and the scrape is best-effort.
    Returns the number of models whose agentic score was set or changed.
    """
    scores_path = get_scores_path(config)
    if not scores_path.exists():
        raise RuntimeError("Processed scores file not found. Please run 'scores fetch' first.")

    indexes = fetch_agentic_indexes()
    if not indexes:
        return 0

    try:
        data = json.loads(scores_path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError("Processed scores file is corrupted.")

    updated = 0
    for slug, entry in data.get("models", {}).items():
        scraped = indexes.get(slug)
        if scraped is None:
            continue
        entry.setdefault("scores", {})
        if scraped is not None:
            scraped = round(scraped, 1)
        entry.setdefault("scores", {})
        if entry["scores"].get("agentic") != scraped:
            entry["scores"]["agentic"] = scraped
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
                # unavailable (scrape gap), so a failed/partial scrape never
                # wipes previously synced agentic data.
                existing_agentic = (variant_info.get("scores") or {}).get("agentic")
                if new_scores.get("agentic") is None and existing_agentic is not None:
                    new_scores["agentic"] = existing_agentic
                variant_info["scores"] = new_scores
                updated_count += 1

    storage.save_models_data(config, models_data)
    return updated_count
