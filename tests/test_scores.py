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

def _mock_page(payload: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(payload).encode()
    mock_response.__enter__.return_value = mock_response
    return mock_response

def test_fetch_agentic_index_from_api_pagination(mock_config):
    """Verify paginated agentic fetch collects slugs and stops at last page."""
    page1 = {
        "data": [
            {"slug": "a", "evaluations": {"artificial_analysis_agentic_index": 25.5}},
            {"slug": "b", "evaluations": {"artificial_analysis_agentic_index": None}},
        ],
        "pagination": {"page": 1, "has_more": True},
    }
    page2 = {
        "data": [{"slug": "c", "evaluations": {"artificial_analysis_agentic_index": 30}}],
        "pagination": {"page": 2, "has_more": False},
    }
    with patch("urllib.request.urlopen", side_effect=[_mock_page(page1), _mock_page(page2)]):
        result = scores.fetch_agentic_index_from_api("fake-key")
    assert result == {"a": 25.5, "c": 30}

def test_fetch_agentic_index_from_api_failure_returns_partial():
    """Verify a failed page returns what was collected (possibly empty)."""
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        assert scores.fetch_agentic_index_from_api("fake-key") == {}

def test_merge_agentic_scores_from_api(tmp_path):
    """Verify merge writes API values and preserves existing ones on gaps."""
    from pathlib import Path

    cfg = AppConfig(data_dir=tmp_path)
    scores_file = tmp_path / "model_scores.json"
    scores_file.write_text(json.dumps({
        "models": {
            "a": {"scores": {"intelligence": 80, "agentic": 10.0}},
            "b": {"scores": {"intelligence": 70, "agentic": 20.0}},
        }
    }))
    with patch(
        "model_manager.domain.scores.fetch_agentic_index_from_api",
        return_value={"a": 25.55},
    ):
        updated = scores.merge_agentic_scores(cfg, "fake-key")
    assert updated == 1
    data = json.loads(scores_file.read_text())
    assert data["models"]["a"]["scores"]["agentic"] == 25.6  # rounded to 1 decimal
    assert data["models"]["b"]["scores"]["agentic"] == 20.0  # gap preserved
