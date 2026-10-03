"""Tests for the static benchmark radar page."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig, RadarConfig, get_radar_output_path
from model_manager.radar import collect_radar_data, generate_radar

runner = CliRunner()


def _cfg(tmp_path: Path, **kwargs) -> AppConfig:
    return AppConfig(data_dir=tmp_path, **kwargs)


def _seed_library(tmp_path: Path) -> None:
    (tmp_path / "models.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "flagship-m": {
                "display_name": "Flagship M", "family": "acme", "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "flagship-m",
                        "scores": {"intelligence": 80.0, "coding": 75.0, "agentic": 70.0, "ttft": 0.4, "tps": 50.0},
                        "tags": ["tier-1"],
                        "provider_ids": {"nvidia": {"acme/flagship-m": {"assessment": "Good", "availability": 1.0, "avg_latency": 300.0}}},
                    }
                },
            },
            "challenger-m": {
                "display_name": "Challenger M", "family": "acme", "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "challenger-m",
                        "scores": {"intelligence": 85.0, "coding": 74.0, "agentic": 70.0, "ttft": 0.2, "tps": 60.0},
                        "tags": ["tier-1"],
                        "provider_ids": {"nvidia": {"acme/challenger-m": {"assessment": "Good", "availability": 1.0, "avg_latency": 200.0}}},
                    }
                },
            },
        },
    }))
    (tmp_path / "model_scores.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "flagship-m": {"name": "Flagship M", "scores": {"intelligence": 80.0, "coding": 75.0, "agentic": 70.0, "ttft": 0.4, "tps": 50.0}},
            "challenger-m": {"name": "Challenger M", "scores": {"intelligence": 85.0, "coding": 74.0, "agentic": 70.0, "ttft": 0.2, "tps": 60.0}},
            "baseline-o": {"name": "Baseline O", "scores": {"intelligence": 95.0, "coding": 90.0, "agentic": 90.0, "ttft": 0.8, "tps": 40.0}},
            "dark-horse": {"name": "Dark Horse", "scores": {"intelligence": 88.0, "coding": 74.5, "agentic": 70.0, "ttft": 0.15, "tps": 70.0}},
        },
    }))
    (tmp_path / "openrouter_free_models.json").write_text(json.dumps({
        "count": 1, "fetched_at": "2026-09-29T00:00:00Z",
        "models": [{"id": "acme/dark-horse", "name": "Dark Horse"}],
    }))


def test_radar_output_path_defaults(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert get_radar_output_path(cfg) == tmp_path / "radar.html"
    cfg.radar = RadarConfig(enabled=True, out_dir=tmp_path / "pub", out_file="r.html")
    assert get_radar_output_path(cfg) == tmp_path / "pub" / "r.html"
    assert get_radar_output_path(cfg, override=tmp_path / "x.html") == tmp_path / "x.html"


def test_radar_empty_library(tmp_path: Path):
    cfg = _cfg(tmp_path)
    out = generate_radar(cfg)
    html = out.read_text()
    assert "Benchmark Radar" in html
    assert "window.__RADAR__" not in html  # payload assigned via var DATA
    assert "__RADAR_PAYLOAD__" not in html  # token fully replaced
    assert "No rows match" in html or "Nothing pinned" in html


def test_radar_collects_proxy_rows_and_recommendations(tmp_path: Path):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    assert data["counts"]["proxy"] == 2
    assert data["flagship_id"] is not None
    # Dark Horse (unmapped candidate) beats the flagship on quality and
    # latency: expect both insight types.
    types = {r["type"] for r in data["recommendations"]}
    assert "QUALITY_DELTA" in types
    html = generate_radar(cfg).read_text()
    assert "Flagship M" in html
    assert "Trade-Off Frontier" in html


def test_radar_aa_index_covers_all_slugs(tmp_path: Path):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    slugs = {"flagship-m", "challenger-m", "baseline-o", "dark-horse"}
    index_rows = [r for r in data["models"] if r["tier"] == "AA_INDEX"]
    # flagship/challenger are library-mapped, dark-horse is a catalog
    # candidate; only scores-only baseline-o lands in the index here.
    assert [r["model"] for r in index_rows] == ["baseline-o"]
    assert data["counts"]["aa_index"] == 1
    html = generate_radar(cfg).read_text()
    assert "AA Index" in html
    assert 'tab:"active"' in html
    # Working set always includes user pins, even on the default tab.
    assert '&& !pinned[m.id]' in html
    # Pins bypass the text filter so they never fall off the chart.
    assert 'if (pinned[m.id]) return true;' in html
    # Stale pins collapse onto live rows instead of duplicating them.
    assert 'function canonicalPinId(id){' in html
    # HUD uses the Intelligence label; no dead TTFT dimension wiring remains.
    assert '<span>Intelligence ' in html
    assert 'data-dim="ttft"' not in html


def test_radar_aa_index_unrepresented_slugs(tmp_path: Path):
    _seed_library(tmp_path)
    (tmp_path / "model_scores.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "lonely-slug": {"name": "Lonely", "scores": {"intelligence": 50.0, "coding": 40.0, "agentic": None, "ttft": 0.5, "tps": 30.0}},
        },
    }))
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    index_rows = [r for r in data["models"] if r["tier"] == "AA_INDEX"]
    assert [r["model"] for r in index_rows] == ["lonely-slug"]
    assert index_rows[0]["scores"]["intelligence"] == 50.0
    assert index_rows[0]["provider_id"] == "lonely-slug"


def test_radar_catalog_carries_scores(tmp_path: Path):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    entry = next(e for e in data["reference_catalog"] if e["slug"] == "baseline-o")
    assert entry["scores"]["intelligence"] == 95.0
    assert "price_per_1m" in entry


def test_radar_scatter_has_axes(tmp_path: Path):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)
    html = generate_radar(cfg).read_text()
    # Axis scaffolding is client-rendered JS: assert the helpers + titles exist.
    for marker in ("niceTicks", "Blended Price ($/1M tokens", "Intelligence",
                   "fmtMoney", "rotate(-90", "no-price rail", "pinLevels",
                   "Pinned reference level"):
        assert marker in html, marker


def test_radar_price_join_and_pins(tmp_path: Path):
    _seed_library(tmp_path)
    (tmp_path / "model_prices.json").write_text(json.dumps({
        "meta": {}, "prices": {"flagship-m": {"blended_per_1m": 3.0, "source": "override"}},
    }))
    cfg = _cfg(tmp_path)
    cfg.radar.pinned_references = ["baseline-o", "nope-missing"]
    data = collect_radar_data(cfg)
    assert data["counts"]["references"] == 1
    assert data["missing_pins"] == ["nope-missing"]
    by_id = {r["id"]: r for r in data["models"]}
    flagship = next(r for r in data["models"] if r["model"] == "flagship-m")
    assert flagship["price_per_1m"] == 3.0
    assert by_id["reference/baseline-o"]["tier"] == "AA_REFERENCE"


def test_radar_cli_no_open(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{tmp_path}"\n')
    out_file = tmp_path / "radar.html"
    result = runner.invoke(app, ["radar", "--out", str(out_file), "--no-open", "-c", str(cfg_file)])
    assert result.exit_code == 0, result.output
    assert out_file.exists()
    assert "Radar written to" in result.output


def test_radar_table_sorting(tmp_path: Path):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)
    html = generate_radar(cfg).read_text()

    # Assert headers have data-sort attributes for Model, Status, Intelligence, Coding, Agentic
    for col in ("model", "status", "intelligence", "coding", "agentic"):
        assert f'data-sort="{col}"' in html, col

    # Assert sorting JS state & functions exist
    for marker in ('sortCol:null', 'sortDir:"asc"', "getStatusLabel", "updateSortHeaders", "#matrix th[data-sort]"):
        assert marker in html, marker
