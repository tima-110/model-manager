"""Tests for LiteLLM restart request functionality."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig
from model_manager.domain.restart import request_restart

runner = CliRunner()


def test_domain_request_restart(tmp_path: Path):
    """Verify domain request_restart appends JSON lines with timestamp and reason."""
    restart_file = tmp_path / "restart_requests.jsonl"
    cfg = AppConfig(litellm_restart_request_path=restart_file)

    # First restart request
    rec1 = request_restart(cfg, reason="Config updated")
    assert rec1["reason"] == "Config updated"
    assert "timestamp" in rec1

    # Second restart request
    rec2 = request_restart(cfg)
    assert rec2["reason"] == "CLI restart request"

    lines = restart_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    data1 = json.loads(lines[0])
    data2 = json.loads(lines[1])

    assert data1["reason"] == "Config updated"
    assert data2["reason"] == "CLI restart request"


def test_cli_request_restart_help():
    """Verify litellm request-restart --help works."""
    result = runner.invoke(app, ["litellm", "request-restart", "--help"])
    assert result.exit_code == 0
    assert "Request a restart of the LiteLLM service" in result.stdout


def test_cli_request_restart(tmp_path: Path):
    """Verify litellm request-restart command appends log entry."""
    cfg_file = tmp_path / "config.toml"
    restart_file = tmp_path / "service" / "restart.jsonl"
    cfg_file.write_text(f'litellm_restart_request_path = "{restart_file}"\n')

    result = runner.invoke(
        app,
        ["litellm", "request-restart", "--config", str(cfg_file), "--reason", "Manual test restart"],
    )
    assert result.exit_code == 0
    assert "Successfully logged restart request" in result.stdout

    assert restart_file.exists()
    lines = restart_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["reason"] == "Manual test restart"
