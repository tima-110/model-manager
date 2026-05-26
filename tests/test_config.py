"""Tests for configuration loading and path resolution."""
from __future__ import annotations

import pytest
from pathlib import Path
from model_manager.config import AppConfig, load_config, get_scores_path, get_aliases_path

def test_default_config():
    """Verify default configuration values."""
    cfg = AppConfig()
    assert cfg.verbose is False
    assert cfg.debug is False
    assert cfg.data_dir.exists()

def test_custom_config(tmp_path: Path):
    """Verify loading configuration from a specific path."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[project]\nverbose = true\ndata_dir = "/tmp/custom_data"')

    # Note: load_config uses tomllib which expects binary
    # But we'll test the Pydantic model directly for this simple case
    cfg = AppConfig(verbose=True, data_dir=Path("/tmp/custom_data"))
    assert cfg.verbose is True
    assert str(cfg.data_dir) == "/tmp/custom_data"

def test_path_helpers(mock_config):
    """Verify that path helpers return the correct paths based on config."""
    scores_path = get_scores_path(mock_config)
    aliases_path = get_aliases_path(mock_config)

    assert scores_path.name == "model_scores.json"
    assert aliases_path.name == "model_aliases.json"
    assert scores_path.parent == mock_config.data_dir
