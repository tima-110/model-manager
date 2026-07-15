"""Tests for LiteLLM cost map build logic."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from model_manager.config import AppConfig
from model_manager.domain import cost_map

def test_merge_cost_maps_basic():
    """Verify that local overrides replace upstream values."""
    upstream = {
        "model-a": {"price": 0.01, "context": 4096},
        "model-b": {"price": 0.02, "context": 8192},
    }
    overrides = {
        "model-a": {"price": 0.005},  # Partial override
        "model-c": {"price": 0.03, "context": 16384},  # New model
    }

    merged = cost_map.merge_cost_maps(upstream, overrides)

    # model-a should have updated price but preserved context
    assert merged["model-a"]["price"] == 0.005
    assert merged["model-a"]["context"] == 4096
    # model-b should be untouched
    assert merged["model-b"]["price"] == 0.02
    # model-c should be added
    assert merged["model-c"]["price"] == 0.03

def test_merge_cost_maps_meta():
    """Verify that _meta is handled separately and doesn't treat it as a model."""
    upstream = {
        "_meta": {"version": "1.0"},
        "model-a": {"price": 0.01},
    }
    overrides = {
        "_meta": {"version": "1.1", "author": "local"},
        "model-a": {"price": 0.005},
    }

    merged = cost_map.merge_cost_maps(upstream, overrides)
    assert merged["_meta"]["version"] == "1.1"
    assert merged["_meta"]["author"] == "local"
    assert merged["model-a"]["price"] == 0.005

def test_fetch_upstream_cost_map_success():
    """Verify that fetch_upstream_cost_map parses valid JSON."""
    mock_data = json.dumps({"model-a": {"price": 0.01}}).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = mock_data
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = cost_map.fetch_upstream_cost_map("https://example.com/cost.json")
        assert result == {"model-a": {"price": 0.01}}

def test_fetch_upstream_cost_map_invalid_json():
    """Verify that malformed JSON raises a RuntimeError."""
    mock_data = b"not json"

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = mock_data
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with pytest.raises(RuntimeError, match="Upstream cost map is not valid JSON"):
            cost_map.fetch_upstream_cost_map("https://example.com/cost.json")

def test_build_local_cost_map_end_to_end(tmp_path, mock_config):
    """
    Verify the full build process:
    Fetch (mocked) -> Load Overrides (file) -> Merge -> Write (file).
    """
    # Setup config to use temp dir
    mock_config.data_dir = tmp_path / "data"
    mock_config.data_dir.mkdir(parents=True, exist_ok=True)
    mock_config.litellm_service_dir = tmp_path / "service"

    # Create a local overrides file
    overrides_path = mock_config.data_dir / "litellm_cost_overrides.json"
    overrides_data = {
        "model-a": {"price": 0.001},
        "custom-model": {"price": 0.05}
    }
    overrides_path.write_text(json.dumps(overrides_data))

    # Mock the network fetch
    upstream_data = {
        "model-a": {"price": 0.01, "context": 4096},
        "model-b": {"price": 0.02, "context": 8192},
    }

    with patch("model_manager.domain.cost_map.fetch_upstream_cost_map") as mock_fetch:
        mock_fetch.return_value = upstream_data

        output_path = cost_map.build_local_cost_map(mock_config)

        assert output_path.exists()
        assert output_path.name == "model_prices_and_context_window.json"

        result = json.loads(output_path.read_text())

        # model-a should be overridden
        assert result["model-a"]["price"] == 0.001
        assert result["model-a"]["context"] == 4096
        # model-b should be preserved
        assert result["model-b"]["price"] == 0.02
        # custom-model should be added
        assert result["custom-model"]["price"] == 0.05
