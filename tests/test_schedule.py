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


def _fake_provider(name: str = "NVIDIA") -> MagicMock:
    provider = MagicMock()
    provider.name = name
    return provider


def test_execute_schedule_pipeline(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    fake_providers = [_fake_provider("NVIDIA"), _fake_provider("Gemini")]

    with patch("model_manager.domain.scores.get_api_key", return_value="test-key"), \
         patch("model_manager.domain.scores.fetch_aa_data", return_value={"models": []}), \
         patch("model_manager.domain.scores.process_aa_data"), \
         patch("model_manager.domain.scores.merge_agentic_scores"), \
         patch("model_manager.domain.scores.sync_scores_to_models", return_value=5), \
         patch("model_manager.domain.providers.list_providers", return_value=fake_providers), \
         patch("model_manager.domain.providers.run_discovery_workflow") as mock_fetch, \
         patch(
             "model_manager.domain.providers.scan_provider_models",
             return_value={"scanned": 3, "cycles": 2},
         ) as mock_scan, \
         patch(
             "model_manager.domain.generate_all.run_generate_all",
             return_value={"steps": ["generate fallbacks: success"], "errors": []},
         ) as mock_gen, \
         patch(
             "model_manager.domain.restart.request_restart",
             return_value={"timestamp": "2026-01-01T00:00:00Z"},
         ) as mock_restart, \
         patch(
             "model_manager.domain.schedule.generate_dashboard",
             return_value=tmp_path / "dashboard.html",
         ) as mock_dash:

        res = schedule.execute_schedule_pipeline(cfg)

        assert "scores fetch: success" in res["steps"]
        assert "scores sync: updated 5 variants" in res["steps"]
        assert mock_fetch.call_count == 2
        assert "providers fetch-all: completed for 2/2 providers" in res["steps"]
        # scan honors the configured max_scans (default 2)
        assert mock_scan.call_count == 2
        assert mock_scan.call_args_list[0].kwargs["max_scans"] == 2
        assert "providers scan-all: completed for 2/2 providers" in res["steps"]
        mock_gen.assert_called_once()
        assert "litellm generate fallbacks: success" in res["steps"]
        mock_restart.assert_called_once()
        assert any("litellm request-restart" in s for s in res["steps"])
        mock_dash.assert_called_once()
        assert any(s.startswith("dashboard: wrote") for s in res["steps"])
        assert res["errors"] == []


def test_execute_schedule_pipeline_fetch_error_continues(tmp_path: Path):
    """A failing provider fetch is recorded but does not stop the pipeline."""
    cfg = AppConfig(data_dir=tmp_path)

    with patch("model_manager.domain.scores.get_api_key", return_value=None), \
         patch("model_manager.domain.scores.sync_scores_to_models", return_value=0), \
         patch(
             "model_manager.domain.providers.list_providers",
             return_value=[_fake_provider("NVIDIA")],
         ), \
         patch(
             "model_manager.domain.providers.run_discovery_workflow",
             side_effect=RuntimeError("no key"),
         ), \
         patch(
             "model_manager.domain.providers.scan_provider_models",
             return_value={"scanned": 0, "cycles": 0},
         ), \
         patch(
             "model_manager.domain.generate_all.run_generate_all",
             return_value={"steps": [], "errors": []},
         ), \
         patch(
             "model_manager.domain.restart.request_restart",
             return_value={"timestamp": "2026-01-01T00:00:00Z"},
         ), \
         patch(
             "model_manager.domain.schedule.generate_dashboard",
             return_value=tmp_path / "dashboard.html",
         ):

        res = schedule.execute_schedule_pipeline(cfg)

        assert "providers fetch (NVIDIA): no key" in res["errors"]
        assert "providers fetch-all: completed for 0/1 providers" in res["steps"]
        # restart reason reflects the earlier errors
        assert any("litellm request-restart" in s for s in res["steps"])
        assert any(s.startswith("dashboard: wrote") for s in res["steps"])


def test_install_schedule_rejects_bad_time(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    with pytest.raises(ValueError, match="Invalid time"):
        schedule.install_schedule(cfg, time="25:99", config_path=tmp_path / "config.toml")
    with pytest.raises(ValueError, match="Invalid time"):
        schedule.install_schedule(cfg, time="not-a-time", config_path=tmp_path / "config.toml")


def test_install_schedule_rejects_bad_frequency(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    with pytest.raises(ValueError, match="Invalid frequency"):
        schedule.install_schedule(cfg, frequency="minutely", config_path=tmp_path / "config.toml")


def test_cli_schedule_install_rejects_bad_time(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    result = runner.invoke(app, ["schedule", "install", "-t", "99:99", "-c", str(cfg_file)])
    assert result.exit_code == 1
    assert "Invalid time" in result.stdout


def test_assess_scan_results():
    from model_manager.domain.discovery import PingResult
    from model_manager.domain.providers import assess_scan_results

    assert assess_scan_results([]) == "Unknown"
    good = [PingResult(status="up", latency_ms=100.0, code="200")] * 10
    assert assess_scan_results(good) == "Good"
    assert assess_scan_results([PingResult(status="unsupported", latency_ms=0.0, code="404")]) == "Unsupported"
    dead = [PingResult(status="down", latency_ms=0.0, code="500")] * 3
    assert assess_scan_results(dead) == "Dead"


def _write_provider_cache(cfg, provider_name: str, model_ids: list[str]) -> None:
    import json
    from model_manager.domain import providers as providers_mod

    provider = next(p for p in providers_mod.list_providers() if p.name == provider_name)
    cache_path = provider.path_fn(cfg)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"models": [{"id": mid} for mid in model_ids]}))


def test_scan_provider_models_missing_cache(tmp_path: Path):
    from model_manager.domain import providers as providers_mod

    cfg = AppConfig(data_dir=tmp_path)
    provider = next(p for p in providers_mod.list_providers() if p.name == "NVIDIA")
    with pytest.raises(RuntimeError, match="cache not found"):
        providers_mod.scan_provider_models(provider, cfg, max_scans=1)


def test_scan_provider_models_success(tmp_path: Path):
    import json
    from model_manager.domain.discovery import PingResult
    from model_manager.domain import providers as providers_mod

    cfg = AppConfig(data_dir=tmp_path, providers={"nvidia": {"cycle_delay_sec": 0}})
    (tmp_path / "models.json").write_text(json.dumps({"models": {}}))
    _write_provider_cache(cfg, "NVIDIA", ["model-a"])
    provider = next(p for p in providers_mod.list_providers() if p.name == "NVIDIA")

    cycles_seen: list[int] = []
    with patch("model_manager.domain.auth.get_secret", return_value="key"), \
         patch(
             "model_manager.domain.discovery.scan_models",
             return_value={"model-a": PingResult(status="up", latency_ms=50.0, code="200")},
         ) as mock_scan:
        summary = providers_mod.scan_provider_models(
            provider, cfg, max_scans=2,
            on_cycle=lambda cyc, res, hist: cycles_seen.append(cyc),
        )

    assert mock_scan.call_count == 2
    assert cycles_seen == [1, 2]
    assert summary["scanned"] == 1
    assert summary["cycles"] == 2
    assert summary["assessments"] == {"model-a": "Good"}
    assert (tmp_path / "nvidia_scan.json").exists()


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
