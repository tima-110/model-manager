"""Tests for the init bootstrap command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import load_config

runner = CliRunner()


def test_init_creates_default_config(tmp_path: Path, monkeypatch):
    import platformdirs

    target = tmp_path / "config.toml"
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda _app: str(tmp_path))
    with patch("model_manager.domain.auth.get_secret", return_value="x"):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert target.exists()
    assert "Created default config" in result.stdout
    assert "All required keys are stored." in result.stdout
    cfg = load_config(target)
    assert cfg.scan_count == 24


def test_init_writes_to_custom_path(tmp_path: Path):
    target = tmp_path / "custom.toml"
    with patch("model_manager.domain.auth.get_secret", return_value="x"):
        result = runner.invoke(app, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    assert "Created default config" in result.stdout


def test_init_never_overwrites(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text('scan_count = 99\n')
    with patch("model_manager.domain.auth.get_secret", return_value="x"):
        result = runner.invoke(app, ["init", "--config", str(target)])
    assert result.exit_code == 0
    assert "already exists" in result.stdout
    assert "scan_count = 99" in target.read_text()


def test_init_lists_exact_commands_for_missing_keys(tmp_path: Path):
    def fake_secret(name: str):
        return "present" if name == "ARTIFICIAL_ANALYSIS_API_KEY" else None

    target = tmp_path / "config.toml"
    with patch("model_manager.domain.auth.get_secret", side_effect=fake_secret):
        result = runner.invoke(app, ["init", "--config", str(target)])
    assert result.exit_code == 0
    # exact copy-pasteable commands for what's missing...
    assert "model-manager auth set NVIDIA_API_KEY=<Add your NVIDIA key>" in result.stdout
    assert "model-manager auth set HF_TOKEN=<Add your HuggingFace key>" in result.stdout
    assert "model-manager auth set GEMINI_API_KEY=<Add your Gemini key>" in result.stdout
    # ...and none for what's stored
    assert "auth set ARTIFICIAL_ANALYSIS_API_KEY=" not in result.stdout
    # next steps shown
    assert "providers fetch-all" in result.stdout
    assert "schedule install" in result.stdout
