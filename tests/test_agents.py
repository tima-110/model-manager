"""Tests for autonomous agent runtime models and CLI commands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig
from model_manager.domain import agents as agents_mod

runner = CliRunner()


@pytest.fixture
def mock_agent_config(tmp_path: Path) -> AppConfig:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    models = {
        "models": {
            "antigravity": {
                "display_name": "Antigravity Agent",
                "family": "gemini",
                "variants": {
                    "standard": {
                        "tags": ["agent"],
                        "provider_ids": {
                            "gemini": {
                                "antigravity-preview-09-2026": {
                                    "availability": 1.0,
                                    "avg_latency": 50.0,
                                    "assessment": "Good",
                                },
                                "limits": {
                                    "rpm": 60,
                                    "tpm": 100000,
                                },
                            }
                        },
                        "notes": "Stateful agent",
                    }
                },
                "default_variant": "standard",
            },
            "chat-bot": {
                "display_name": "Standard Chatbot",
                "family": "gemini",
                "variants": {
                    "standard": {
                        "tags": ["tier-1"],
                        "provider_ids": {
                            "gemini": {
                                "gemini-2.5-pro": {},
                            }
                        },
                    }
                },
            },
        }
    }
    (data_dir / "models.json").write_text(json.dumps(models))

    # Provider cache with an extra unmapped preview
    gemini_cache = {
        "models": [
            {"id": "antigravity-preview-09-2026", "name": "Antigravity 09-2026"},
            {"id": "antigravity-preview-latest", "name": "Antigravity Latest Preview"},
        ]
    }
    (data_dir / "gemini_available_models.json").write_text(json.dumps(gemini_cache))

    return AppConfig(
        data_dir=data_dir,
        providers={"gemini": {"keys": ["GEMINI_KEY_TEST"], "litellm_prefix": "gemini"}},
    )


def test_list_agents(mock_agent_config: AppConfig):
    mapped_only = agents_mod.list_agents(mock_agent_config, include_unmapped=False)
    assert len(mapped_only) == 1
    assert mapped_only[0]["alias"] == "antigravity"
    assert mapped_only[0]["model_id"] == "antigravity-preview-09-2026"
    assert mapped_only[0]["limits"]["rpm"] == 60
    assert mapped_only[0]["mapped"] is True

    all_agents = agents_mod.list_agents(mock_agent_config, include_unmapped=True)
    assert len(all_agents) == 2
    model_ids = {a["model_id"] for a in all_agents}
    assert "antigravity-preview-09-2026" in model_ids
    assert "antigravity-preview-latest" in model_ids


def test_resolve_agent_target(mock_agent_config: AppConfig):
    # Match by alias
    res1 = agents_mod.resolve_agent_target(mock_agent_config, "antigravity")
    assert res1["model_id"] == "antigravity-preview-09-2026"
    assert res1["provider"] == "gemini"

    # Match by provider model ID
    res2 = agents_mod.resolve_agent_target(mock_agent_config, "antigravity-preview-09-2026")
    assert res2["alias"] == "antigravity"

    # Match by full target path
    res3 = agents_mod.resolve_agent_target(mock_agent_config, "gemini/antigravity-preview-09-2026")
    assert res3["alias"] == "antigravity"


def test_run_interaction_proxy_payload(mock_agent_config: AppConfig):
    mock_resp_data = {
        "interaction_id": "test-session-123",
        "status": "completed",
        "outputs": [{"text": "Task finished successfully."}],
    }
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen, \
         patch("model_manager.domain.auth.get_secret", return_value="master-key-xyz"):

        res = agents_mod.run_interaction(
            mock_agent_config,
            agent="antigravity",
            prompt="Run pytests",
            tools=["code_execution"],
            direct=False,
            base_url="http://localhost:4000",
        )

        assert res["success"] is True
        assert res["interaction_id"] == "test-session-123"
        assert res["outputs"][0]["text"] == "Task finished successfully."

        # Inspect request
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "http://localhost:4000/v1beta/interactions"
        assert called_req.headers["Authorization"] == "Bearer master-key-xyz"

        payload = json.loads(called_req.data.decode("utf-8"))
        assert payload["agent"] == "antigravity"
        assert payload["input"] == "Run pytests"
        assert payload["tools"] == [{"type": "code_execution"}]


def test_run_interaction_direct_payload(mock_agent_config: AppConfig):
    mock_resp_data = {
        "interaction_id": "direct-session-456",
        "status": "completed",
        "outputs": [{"text": "Direct task output."}],
    }
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen, \
         patch("model_manager.domain.auth.get_secret", return_value="gemini-key-123"):

        res = agents_mod.run_interaction(
            mock_agent_config,
            agent="antigravity",
            prompt="Direct code analysis",
            direct=True,
            previous_interaction_id="prev-789",
        )

        assert res["success"] is True
        assert res["interaction_id"] == "direct-session-456"

        called_req = mock_urlopen.call_args[0][0]
        assert "generativelanguage.googleapis.com/v1beta/interactions?key=gemini-key-123" in called_req.full_url
        assert "Authorization" not in called_req.headers

        payload = json.loads(called_req.data.decode("utf-8"))
        assert payload["agent"] == "antigravity-preview-09-2026"
        assert payload["previous_interaction_id"] == "prev-789"


def test_cli_agents_list(tmp_path: Path, mock_agent_config: AppConfig):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{mock_agent_config.data_dir}"\n')

    result = runner.invoke(app, ["agents", "list", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "antigravity" in result.stdout

    result_json = runner.invoke(app, ["agents", "list", "--json", "--config", str(cfg_file)])
    assert result_json.exit_code == 0
    parsed = json.loads(result_json.stdout)
    assert len(parsed) == 1
    assert parsed[0]["alias"] == "antigravity"
    assert parsed[0]["target"] == "gemini/antigravity-preview-09-2026"



def test_cli_agents_run(tmp_path: Path, mock_agent_config: AppConfig):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{mock_agent_config.data_dir}"\n')

    fake_result = {
        "success": True,
        "status_code": 200,
        "latency_ms": 150.0,
        "agent": "antigravity",
        "interaction_id": "session-cli-999",
        "outputs": [{"text": "Hello from Antigravity agent!"}],
        "debug": None,
    }

    with patch("model_manager.domain.agents.run_interaction", return_value=fake_result):
        res = runner.invoke(app, ["agents", "run", "Test prompt", "--config", str(cfg_file)])
        assert res.exit_code == 0
        assert "Interaction Completed" in res.stdout
        assert "session-cli-999" in res.stdout
        assert "Hello from Antigravity agent!" in res.stdout
