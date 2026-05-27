"""Shared fixtures for model-manager tests."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from model_manager.config import AppConfig

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with sample files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Sample scores
    scores = {
        "meta": {"total_models": 1},
        "models": {
            "gemma-4-31b": {
                "name": "Gemma 4 31B",
                "scores": {"intelligence": 40, "coding": 30, "math": 20},
                "last_synced": "2026-01-01T00:00:00Z"
            }
        }
    }
    (data_dir / "model_scores.json").write_text(json.dumps(scores))

    # Sample aliases
    aliases = {
        "models": {
            "gemma-4-31b": {
                "display_name": "Gemma 4 31B",
                "family": "google",
                "variants": {
                    "standard": {
                        "aa_slug": "gemma-4-31b",
                        "provider_ids": {"google": ["google/gemma-4-31b"]},
                        "notes": ""
                    }
                },
                "default_variant": "standard"
            }
        }
    }
    (data_dir / "models.json").write_text(json.dumps(aliases))

    return data_dir

@pytest.fixture
def mock_config(tmp_data_dir: Path) -> AppConfig:
    """Return an AppConfig pointing to the temporary data directory."""
    return AppConfig(data_dir=tmp_data_dir)
