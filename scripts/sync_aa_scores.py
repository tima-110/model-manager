#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
from datetime import datetime

# Configuration
DATA_DIR = "data"
RAW_SCORES_PATH = os.path.join(DATA_DIR, "aa_raw_response.json")
PROCESSED_SCORES_PATH = os.path.join(DATA_DIR, "model_scores.json")
SECRET_PATH = ".env"
AGENTIC_INDEX_URL = "https://artificialanalysis.ai/models/capabilities/agentic"

def get_api_key():
    """Load AA API key from .env or environment variables."""
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r") as f:
            for line in f:
                if line.strip().startswith("ARTIFICIAL_ANALYSIS_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY")

def fetch_aa_data(api_key):
    """Fetch model data from Artificial Analysis API and save raw response."""
    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    headers = {"x-api-key": api_key}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

            # Save raw response for traceability
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(RAW_SCORES_PATH, "w") as f:
                json.dump(data, f, indent=2)

            return data
    except Exception as e:
        print(f"Error fetching AA data: {e}")
        return None

def fetch_agentic_indexes():
    """Scrape {slug: agentic_index} from the AA Agentic Index page."""
    import re
    try:
        req = urllib.request.Request(AGENTIC_INDEX_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode()
    except Exception as e:
        print(f"Error fetching AA agentic index: {e}")
        return {}

    rows = re.findall(r'self\.__next_f\.push\(\[1,"(.*)"\]\)', html)
    blob = "".join(rows)
    starts = list(re.finditer(r'\\"id\\":\\"[0-9a-f]{8}-[0-9a-f-]{27,36}\\",\\"slug\\":\\"', blob))
    indexes = {}
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

def process_aa_data(aa_response, agentic_indexes=None):
    """Transform API response into a slug-keyed dictionary of scores."""
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
            },
            "last_synced": processed["meta"]["last_updated"]
        }

    return processed

def main():
    api_key = get_api_key()
    if not api_key:
        print("CRITICAL: No ARTIFICIAL_ANALYSIS_API_KEY found in .env or environment variables.")
        sys.exit(1)

    print("Fetching data from Artificial Analysis...")
    aa_response = fetch_aa_data(api_key)
    if not aa_response:
        print("CRITICAL: Failed to retrieve data from Artificial Analysis.")
        sys.exit(1)

    print("Processing and saving scores...")
    processed_scores = process_aa_data(aa_response)
    if not processed_scores:
        print("CRITICAL: Failed to process Artificial Analysis data.")
        sys.exit(1)

    with open(PROCESSED_SCORES_PATH, "w") as f:
        json.dump(processed_scores, f, indent=2)

    print("Scraping agentic index...")
    agentic_indexes = fetch_agentic_indexes()
    merged = 0
    processed_scores = json.load(open(PROCESSED_SCORES_PATH))
    for slug, entry in processed_scores.get("models", {}).items():
        scraped = agentic_indexes.get(slug)
        if scraped is None:
            continue
        entry.setdefault("scores", {})
        if entry["scores"].get("agentic") != scraped:
            entry["scores"]["agentic"] = scraped
            merged += 1
    with open(PROCESSED_SCORES_PATH, "w") as f:
        json.dump(processed_scores, f, indent=2)

    print(f"\nDone. Synced {processed_scores['meta']['total_models']} models to {PROCESSED_SCORES_PATH}.")
    if merged:
        print(f"Updated agentic index for {merged} models.")

if __name__ == "__main__":
    main()
