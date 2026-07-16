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

def test_merge_cost_maps_overrides_after_sample_spec():
    """Verify overrides are inserted after sample_spec, not at the end."""
    upstream = {
        "sample_spec": {"description": "Sample"},
        "model-a": {"price": 0.01},
        "model-z": {"price": 0.99},
    }
    overrides = {
        "custom-model": {"price": 0.05},
        "another-custom": {"price": 0.10},
    }

    merged = cost_map.merge_cost_maps(upstream, overrides)
    keys = list(merged.keys())

    # sample_spec should still be first
    assert keys[0] == "sample_spec"
    # Overrides should be next, before other upstream entries
    assert "custom-model" in keys
    assert "another-custom" in keys
    # Verify ordering: overrides come right after sample_spec
    sample_idx = keys.index("sample_spec")
    custom_idx = keys.index("custom-model")
    model_a_idx = keys.index("model-a")
    assert custom_idx > sample_idx, "Overrides should come after sample_spec"
    assert custom_idx < model_a_idx, "Overrides should come before other upstream entries"

def test_merge_cost_maps_no_sample_spec():
    """Verify behavior when upstream has no sample_spec entry."""
    upstream = {
        "model-a": {"price": 0.01},
    }
    overrides = {
        "custom-model": {"price": 0.05},
    }

    merged = cost_map.merge_cost_maps(upstream, overrides)
    keys = list(merged.keys())

    # Without sample_spec, overrides should be first
    assert keys[0] == "custom-model"
    assert merged["model-a"]["price"] == 0.01

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

    # Mock the network fetch with sample_spec as first entry
    upstream_data = {
        "sample_spec": {"description": "Sample"},
        "model-a": {"price": 0.01, "context": 4096},
        "model-b": {"price": 0.02, "context": 8192},
    }

    with patch("model_manager.domain.cost_map.fetch_upstream_cost_map") as mock_fetch:
        mock_fetch.return_value = upstream_data

        output_path = cost_map.build_local_cost_map(mock_config)

        assert output_path.exists()
        assert output_path.name == "model_prices_and_context_window.json"

        result = json.loads(output_path.read_text())
        keys = list(result.keys())

        # Verify ordering: sample_spec first, then overrides, then upstream
        assert keys[0] == "sample_spec"
        assert "model-a" in keys
        assert "model-b" in keys
        assert "custom-model" in keys

        # model-a should be overridden
        assert result["model-a"]["price"] == 0.001
        assert result["model-a"]["context"] == 4096
        # model-b should be preserved
        assert result["model-b"]["price"] == 0.02
        # custom-model should be added
        assert result["custom-model"]["price"] == 0.05