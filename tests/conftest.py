"""Shared fixtures for model-manager tests."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from model_manager.config import AppConfig, find_config


@pytest.fixture(autouse=True)
def _isolate_user_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never read/write real user config, data, or keyring."""
    config_home = tmp_path / "xdg_config"
    data_home = tmp_path / "xdg_data"
    config_home.mkdir(parents=True, exist_ok=True)
    data_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setattr("model_manager.domain.auth.get_secret", lambda _key_name: None)

    resolved = find_config()
    assert resolved is None or resolved.is_relative_to(tmp_path), (
        f"Test isolation regression: find_config() resolved to {resolved}"
    )

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
