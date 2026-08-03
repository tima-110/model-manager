"""Tests for the litellm command group."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from model_manager.cli import app

runner = CliRunner()


def test_litellm_config_check_help():
    """Verify litellm config check --help works."""
    result = runner.invoke(app, ["litellm", "config", "check", "--help"])
    assert result.exit_code == 0


def test_litellm_config_check_valid_yaml(tmp_path: Path):
    """Verify valid YAML passes."""
    cfg_file = tmp_path / "config.toml"
    litellm_cfg = tmp_path / "litellm.yaml"
    litellm_cfg.write_text("general:\n  port: 4000\n")
    cfg_file.write_text(f'litellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "PASS" in result.stdout


def test_litellm_config_check_invalid_yaml(tmp_path: Path):
    """Verify invalid YAML fails."""
    cfg_file = tmp_path / "config.toml"
    litellm_cfg = tmp_path / "litellm.yaml"
    litellm_cfg.write_text("key: [unclosed bracket\n")
    cfg_file.write_text(f'litellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_litellm_config_check_missing(tmp_path: Path):
    """Verify missing file fails."""
    cfg_file = tmp_path / "config.toml"
    litellm_cfg = tmp_path / "nonexistent" / "litellm.yaml"
    cfg_file.write_text(f'litellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout