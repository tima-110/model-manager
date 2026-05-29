"""Tests for AA score ingestion and processing."""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from model_manager.domain import scores
from model_manager.config import AppConfig

def test_process_aa_data(mock_config):
    """Verify that raw API data is correctly processed into the scores format."""
    sample_response = {
        "data": [
            {
                "slug": "model-1",
                "name": "Model One",
                "median_time_to_first_token_seconds": 0.5,
                "median_output_tokens_per_second": 100.0,
                "evaluations": {
                    "artificial_analysis_intelligence_index": 80,
                    "artificial_analysis_coding_index": 70,
                    "artificial_analysis_math_index": 60
                }
            }
        ]
    }

    processed = scores.process_aa_data(sample_response, mock_config)
    assert processed is not None
    assert "model-1" in processed["models"]
    assert processed["models"]["model-1"]["scores"]["intelligence"] == 80
    assert processed["models"]["model-1"]["scores"]["ttft"] == 0.5
    assert processed["models"]["model-1"]["scores"]["tps"] == 100.0
    assert processed["meta"]["total_models"] == 1

def test_fetch_aa_data_success(mock_config):
    """Verify successful data fetch and raw save."""
    mock_data = json.dumps({"data": []}).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        # Setup the mock context manager
        mock_response = MagicMock()
        mock_response.read.return_value = mock_data
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = scores.fetch_aa_data("fake-key", mock_config)
        assert result == {"data": []}
        assert (mock_config.data_dir / "aa_raw_response.json").exists()

def test_fetch_aa_data_failure(mock_config):
    """Verify that network errors are handled gracefully."""
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        result = scores.fetch_aa_data("fake-key", mock_config)
        assert result is None
