"""Tests for schedule domain logic and CLI commands."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig, ScheduleConfig, load_config
from model_manager.domain import schedule

runner = CliRunner()


@pytest.fixture
def mock_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path)


def test_install_schedule_linux(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    cfg_file = tmp_path / "config.toml"

    with patch("platform.system", return_value="Linux"), \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_sub:

        details = schedule.install_schedule(
            cfg,
            frequency="daily",
            time="03:15",
            max_scans=2,
            config_path=cfg_file,
        )

        assert details["enabled"] is True
        assert details["frequency"] == "daily"
        assert details["time"] == "03:15"
        assert details["max_scans"] == 2

        service_file = tmp_path / ".config" / "systemd" / "user" / "model-manager-schedule.service"
        timer_file = tmp_path / ".config" / "systemd" / "user" / "model-manager-schedule.timer"

        assert service_file.exists()
        assert timer_file.exists()
        assert "OnCalendar=*-*-* 03:15:00" in timer_file.read_text()
        assert mock_sub.called

        # Verify config saved
        reloaded = load_config(cfg_file)
        assert reloaded.schedule.enabled is True
        assert reloaded.schedule.frequency == "daily"
        assert reloaded.schedule.time == "03:15"


def test_install_schedule_macos(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    cfg_file = tmp_path / "config.toml"

    with patch("platform.system", return_value="Darwin"), \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run") as mock_sub:

        details = schedule.install_schedule(
            cfg,
            frequency="weekly",
            time="04:00",
            max_scans=1,
            config_path=cfg_file,
        )

        assert details["os"] == "darwin"
        plist_file = tmp_path / "Library" / "LaunchAgents" / "com.model-manager.schedule.plist"
        assert plist_file.exists()
        plist_content = plist_file.read_text()
        assert "com.model-manager.schedule" in plist_content
        assert "<integer>4</integer>" in plist_content


def test_remove_schedule_linux(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, schedule=ScheduleConfig(enabled=True))
    cfg_file = tmp_path / "config.toml"

    service_dir = tmp_path / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_file = service_dir / "model-manager-schedule.service"
    timer_file = service_dir / "model-manager-schedule.timer"
    service_file.write_text("[Unit]")
    timer_file.write_text("[Unit]")

    with patch("platform.system", return_value="Linux"), \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run"):

        details = schedule.remove_schedule(cfg, config_path=cfg_file)

        assert details["enabled"] is False
        assert not service_file.exists()
        assert not timer_file.exists()

        reloaded = load_config(cfg_file)
        assert reloaded.schedule.enabled is False


def test_get_schedule_status(tmp_path: Path):
    cfg = AppConfig(
        data_dir=tmp_path,
        schedule=ScheduleConfig(enabled=True, frequency="hourly", time="01:00", max_scans=2),
    )

    with patch("platform.system", return_value="Linux"), \
         patch("pathlib.Path.home", return_value=tmp_path):

        status = schedule.get_schedule_status(cfg)
        assert status["enabled"] is True
        assert status["frequency"] == "hourly"
        assert status["time"] == "01:00"
        assert status["max_scans"] == 2
        assert status["service_installed"] is False


def test_execute_schedule_pipeline(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)

    with patch("model_manager.domain.scores.get_api_key", return_value="test-key"), \
         patch("model_manager.domain.scores.fetch_aa_data", return_value={"models": []}), \
         patch("model_manager.domain.scores.process_aa_data"), \
         patch("model_manager.domain.scores.merge_agentic_scores"), \
         patch("model_manager.domain.scores.sync_scores_to_models", return_value=5), \
         patch("model_manager.domain.providers.list_providers", return_value=[]), \
         patch("model_manager.domain.fallbacks.generate_fallbacks_yaml"), \
         patch("model_manager.domain.model_group_aliases.generate_aliases_yaml"):

        res = schedule.execute_schedule_pipeline(cfg)

        assert "scores fetch: success" in res["steps"]
        assert "scores sync: updated 5 variants" in res["steps"]
        assert "litellm generate fallbacks: success" in res["steps"]
        assert "litellm generate aliases: success" in res["steps"]


def test_cli_schedule_commands(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"

    # Test CLI schedule install
    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run"):
        result = runner.invoke(app, ["schedule", "install", "-f", "daily", "-t", "03:00", "-m", "2", "-c", str(cfg_file)])
        assert result.exit_code == 0
        assert "Successfully installed model-manager schedule!" in result.stdout

    # Test CLI schedule status
    result = runner.invoke(app, ["schedule", "status", "-c", str(cfg_file)])
    assert result.exit_code == 0
    assert "Schedule Configuration & Status" in result.stdout
    assert "daily" in result.stdout

    # Test CLI schedule run
    with patch("model_manager.domain.schedule.execute_schedule_pipeline", return_value={"steps": ["step1"], "errors": []}):
        result = runner.invoke(app, ["schedule", "run", "-c", str(cfg_file)])
        assert result.exit_code == 0
        assert "Scheduled pipeline completed successfully." in result.stdout

    # Test CLI schedule remove
    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("subprocess.run"):
        result = runner.invoke(app, ["schedule", "remove", "-c", str(cfg_file)])
        assert result.exit_code == 0
        assert "Successfully removed model-manager schedule." in result.stdout
