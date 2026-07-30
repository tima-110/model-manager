"""Tests for the doctor command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_manager.cli import app

runner = CliRunner()


def _write_config(config_dir: Path, data_dir: Path) -> Path:
    """Write a config.toml pointing to the given data_dir."""
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = config_dir / "config.toml"
    cfg_file.write_text(f'data_dir = "{data_dir}"\n')
    return cfg_file


def test_doctor_help():
    """Verify doctor --help works."""
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0


def test_doctor_runs_with_defaults():
    """Verify doctor runs without crashing under default config."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1)
    assert "Health Report" in result.stdout


def test_doctor_verbose(tmp_path: Path):
    """Verify verbose mode shows file sizes."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "model_scores.json").write_text(json.dumps({"meta": {}, "models": {}}))

    cfg_file = _write_config(tmp_path / "config", data_dir)
    result = runner.invoke(app, ["doctor", "--config", str(cfg_file), "--verbose"])
    assert result.exit_code == 0
    assert "bytes" in result.stdout


def test_doctor_corrupt_json(tmp_path: Path):
    """Verify corrupt JSON data files are reported as failures."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "model_scores.json").write_text("not valid json")

    cfg_file = _write_config(tmp_path / "config", data_dir)
    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_doctor_missing_data_dir(tmp_path: Path):
    """Verify missing data directory is handled gracefully."""
    data_dir = tmp_path / "nonexistent"

    cfg_file = _write_config(tmp_path / "config", data_dir)
    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "WARN" in result.stdout


def test_doctor_custom_config(tmp_path: Path):
    """Verify doctor works with a custom config path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    cfg_file = _write_config(tmp_path / "config", data_dir)
    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "Health Report" in result.stdout


def test_doctor_litellm_config_valid_yaml(tmp_path: Path):
    """Verify valid litellm YAML config passes."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg_file = _write_config(tmp_path / "config", data_dir)

    # Create a valid litellm config
    litellm_cfg = tmp_path / "litellm.yaml"
    litellm_cfg.write_text("general:\n  port: 4000\nmodel_list:\n  - model_name: gpt-4\n")
    # Inject the path into config.toml
    cfg_file.write_text(f'data_dir = "{data_dir}"\nlitellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "Valid YAML" in result.stdout


def test_doctor_litellm_config_invalid_yaml(tmp_path: Path):
    """Verify invalid YAML in litellm config is reported as failure."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg_file = _write_config(tmp_path / "config", data_dir)

    litellm_cfg = tmp_path / "litellm.yaml"
    litellm_cfg.write_text("key: [unclosed bracket\n")
    cfg_file.write_text(f'data_dir = "{data_dir}"\nlitellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_doctor_litellm_config_missing(tmp_path: Path):
    """Verify missing litellm config file is handled gracefully."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg_file = _write_config(tmp_path / "config", data_dir)

    litellm_cfg = tmp_path / "nonexistent" / "litellm.yaml"
    cfg_file.write_text(f'data_dir = "{data_dir}"\nlitellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "WARN" in result.stdout


def test_doctor_all_green(tmp_path: Path):
    """Verify all checks pass with a clean, fully-populated environment."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "model_scores.json").write_text(json.dumps({"meta": {}, "models": {}}))
    (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": {}}))
    (data_dir / "aa_raw_response.json").write_text("{}")
    (data_dir / "openrouter_free_models.json").write_text(json.dumps({"models": []}))
    (data_dir / "nvidia_available_models.json").write_text(json.dumps({"models": []}))
    (data_dir / "ollama_available_models.json").write_text(json.dumps({"models": []}))
    (data_dir / "gemini_available_models.json").write_text(json.dumps({"models": []}))
    (data_dir / "litellm_cost_overrides.json").write_text("{}")

    cfg_file = _write_config(tmp_path / "config", data_dir)
    result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "Health Report" in result.stdout