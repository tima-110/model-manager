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
    kwargs.setdefault("litellm_service_dir", tmp_path)
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
    # HUD displays Score and model/variant; no dead TTFT dimension wiring remains.
    assert '<span>Score ' in html
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


def test_radar_variant_aggregation_and_highest_price(tmp_path: Path):
    (tmp_path / "models.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "multi-prov-m": {
                "display_name": "Multi Prov M", "family": "acme", "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "multi-prov-m",
                        "scores": {"intelligence": 82.0, "coding": 78.0, "agentic": 75.0, "ttft": 0.3, "tps": 55.0},
                        "tags": ["tier-1"],
                        "provider_ids": {
                            "nvidia": {"acme/multi-prov-m": {"assessment": "Good", "availability": 1.0, "avg_latency": 300.0}},
                            "openrouter": {"acme/multi-prov-m": {"assessment": "Good", "availability": 0.9, "avg_latency": 250.0}},
                        },
                    }
                },
            },
        },
    }))
    (tmp_path / "model_scores.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "multi-prov-m": {"name": "Multi Prov M", "scores": {"intelligence": 82.0, "coding": 78.0, "agentic": 75.0, "ttft": 0.3, "tps": 55.0}},
        },
    }))
    (tmp_path / "model_prices.json").write_text(json.dumps({
        "meta": {},
        "prices": {
            "nvidia/acme/multi-prov-m": {"blended_per_1m": 1.50, "source": "nvidia"},
            "openrouter/acme/multi-prov-m": {"blended_per_1m": 2.50, "source": "override"},
        },
    }))

    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    proxy_rows = [r for r in data["models"] if r["tier"] == "IN_LITELLM_PROXY"]
    assert len(proxy_rows) == 1
    row = proxy_rows[0]
    assert row["id"] == "multi-prov-m/standard"
    assert row["variant"] == "standard"
    assert row["price_per_1m"] == 2.50  # highest price among openrouter (2.50) and nvidia fallback (1.50)
    assert "nvidia" in row["provider"]
    assert "openrouter" in row["provider"]
    assert row["provider_mappings"] == [
        {"provider": "nvidia", "provider_id": "acme/multi-prov-m"},
        {"provider": "openrouter", "provider_id": "acme/multi-prov-m"},
    ]


def test_radar_yaml_export_emits_one_entry_per_provider(tmp_path: Path):
    _seed_multiprov_library(tmp_path)
    data = collect_radar_data(_cfg(tmp_path))
    row = next(r for r in data["models"] if r["id"] == "multi-prov-m/standard")
    assert row["provider_mappings"] == [
        {"provider": "nvidia", "provider_id": "acme/multi-prov-m"},
        {"provider": "openrouter", "provider_id": "acme/multi-prov-m"},
    ]
    html = generate_radar(_cfg(tmp_path)).read_text()
    # Export iterates provider mappings instead of comma-joined strings.
    assert "m.provider_mappings" in html
    assert "maps.map(function(p){" in html
    # Old provider-level pins collapse onto the live variant row.
    assert 'id.indexOf(m.id + "/") === 0' in html


def _seed_multiprov_library(tmp_path: Path):
    (tmp_path / "models.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "multi-prov-m": {
                "display_name": "Multi Prov M", "family": "acme", "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "multi-prov-m",
                        "scores": {"intelligence": 82.0, "coding": 78.0, "agentic": 75.0, "ttft": 0.3, "tps": 55.0},
                        "tags": ["tier-1"],
                        "provider_ids": {
                            "nvidia": {"acme/multi-prov-m": {"assessment": "Good", "availability": 1.0, "avg_latency": 300.0}},
                            "openrouter": {"acme/multi-prov-m": {"assessment": "Good", "availability": 0.9, "avg_latency": 250.0}},
                        },
                    }
                },
            },
        },
    }))
    (tmp_path / "model_scores.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "multi-prov-m": {"name": "Multi Prov M", "scores": {"intelligence": 82.0, "coding": 78.0, "agentic": 75.0, "ttft": 0.3, "tps": 55.0}},
        },
    }))


def test_radar_reference_sets_elements_and_coloring(tmp_path: Path):
    _seed_library(tmp_path)
    # Write a reference set to models.json
    models_file = tmp_path / "models.json"
    doc = json.loads(models_file.read_text())
    doc["reference_sets"] = {
        "frontier-set": {
            "display_name": "Frontier Set",
            "items": [
                {"model": "baseline-o", "variant": "std", "aa_slug": "baseline-o"}
            ]
        }
    }
    models_file.write_text(json.dumps(doc))

    cfg = _cfg(tmp_path)
    html = generate_radar(cfg).read_text()

    # Assert DOM elements for reference sets exist
    assert 'id="refSetSelect"' in html
    assert 'id="refSetPinBtn"' in html
    assert 'Pin Set' in html

    # Assert reference set functions and colors exist in JS
    assert 'refSetColors' in html
    assert 'getPinnedRefSetColorMap' in html
    assert 'getRefSetColorForModel' in html
    assert 'refset/' in html


def test_radar_prices_from_local_cost_map(tmp_path: Path):
    _seed_library(tmp_path)
    # Write local LiteLLM cost map output file in litellm_service_dir
    cost_map_file = tmp_path / "model_prices_and_context_window.json"
    cost_map_file.write_text(json.dumps({
        "acme/flagship-m": {
            "input_cost_per_token": 1.0e-6,
            "output_cost_per_token": 3.0e-6,
        },
        "acme/challenger-m": {
            "input_cost_per_token": 2.0e-6,
            "output_cost_per_token": 2.0e-6,
        },
    }))
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    flagship = next(r for r in data["models"] if r["model"] == "flagship-m")
    assert flagship["price_per_1m"] == 2.0
    challenger = next(r for r in data["models"] if r["model"] == "challenger-m")
    assert challenger["price_per_1m"] == 2.0


def test_radar_prices_from_upstream_cost_map(tmp_path: Path, monkeypatch):
    _seed_library(tmp_path)
    cfg = _cfg(tmp_path)

    from model_manager.domain import prices as prices_mod
    monkeypatch.setattr(
        prices_mod,
        "fetch_upstream_cost_entries",
        lambda url: {"acme/flagship-m": {"blended_per_1m": 4.5, "source": "litellm-upstream"}},
    )

    data = collect_radar_data(cfg)
    flagship = next(r for r in data["models"] if r["model"] == "flagship-m")
    assert flagship["price_per_1m"] == 4.5


def test_radar_prices_ignores_override_file(tmp_path: Path):
    _seed_library(tmp_path)
    # Write local LiteLLM cost map output file
    (tmp_path / "model_prices_and_context_window.json").write_text(json.dumps({
        "acme/flagship-m": {"input_cost_per_token": 1.0e-6, "output_cost_per_token": 1.0e-6},
    }))
    # Write local overrides file with conflicting price
    (tmp_path / "litellm_cost_overrides.json").write_text(json.dumps({
        "acme/flagship-m": {"input_cost_per_token": 9.0e-6, "output_cost_per_token": 9.0e-6},
    }))
    cfg = _cfg(tmp_path)
    data = collect_radar_data(cfg)
    flagship = next(r for r in data["models"] if r["model"] == "flagship-m")
    # Must use the cost map price (1.0), NOT the override file (9.0)
    assert flagship["price_per_1m"] == 1.0

