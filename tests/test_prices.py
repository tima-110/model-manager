"""Tests for the model price library."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig, get_prices_path
from model_manager.domain import prices as prices_mod

runner = CliRunner()


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path)


def test_merge_precedence_and_lookup():
    merged = prices_mod.merge_entries(
        {"a": {"blended_per_1m": 1.0, "source": "override"}},
        {"a": {"blended_per_1m": 2.0, "source": "openrouter"}, "b": {"blended_per_1m": 5.0, "source": "openrouter"}},
        {"b": {"blended_per_1m": 9.0, "source": "litellm-upstream"}},
    )
    assert merged["a"]["source"] == "override"
    assert merged["b"]["source"] == "openrouter"
    assert prices_mod.lookup_price(["missing", "B"], merged) == 5.0
    assert prices_mod.lookup_price(["missing"], merged) is None


def test_build_library_offline_injected(tmp_path: Path):
    cfg = _cfg(tmp_path)
    (tmp_path / "litellm_cost_overrides.json").write_text(json.dumps({
        "acme/flagship": {"input_cost_per_token": 1e-6, "output_cost_per_token": 3e-6},
    }))
    path = prices_mod.build_price_library(
        cfg,
        openrouter_entries={"acme/flagship": {"blended_per_1m": 9.0, "source": "openrouter"},
                            "other/m": {"blended_per_1m": 0.0, "source": "openrouter"}},
        upstream_entries={"up/m": {"blended_per_1m": 4.0, "source": "litellm-upstream"}},
    )
    assert path == get_prices_path(cfg)
    doc = json.loads(path.read_text())
    assert doc["prices"]["acme/flagship"] == {"blended_per_1m": 2.0, "source": "override"}
    assert doc["prices"]["other/m"]["blended_per_1m"] == 0.0
    assert doc["meta"]["total_entries"] == 3


def test_build_library_graceful_when_sources_fail(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(prices_mod, "fetch_openrouter_prices", _boom)
    monkeypatch.setattr(prices_mod, "fetch_upstream_cost_entries", _boom)
    path = prices_mod.build_price_library(cfg)
    assert json.loads(path.read_text())["prices"] == {}


def test_prices_fetch_cli_offline(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(prices_mod, "fetch_openrouter_prices", lambda: {"a/b": {"blended_per_1m": 1.5, "source": "openrouter"}})
    monkeypatch.setattr(prices_mod, "fetch_upstream_cost_entries", lambda url: {})
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{tmp_path}"\n')
    result = runner.invoke(app, ["prices", "fetch", "-c", str(cfg_file)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "model_prices.json").exists()


def test_prices_list_empty(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{tmp_path}"\n')
    result = runner.invoke(app, ["prices", "list", "-c", str(cfg_file)])
    assert result.exit_code == 0
    assert "prices fetch" in result.output
