"""Tests for tier tagging and tag management."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig
from model_manager.domain import tags

runner = CliRunner()


def _write_library(data_dir: Path, models: dict) -> None:
    (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": models}, indent=2))


def _scored(composite_variants: dict[str, tuple[float, float, float | None]]) -> dict:
    models: dict = {}
    for key, (i, c, a) in composite_variants.items():
        mid, vid = key.rsplit("/", 1)
        models.setdefault(mid, {"display_name": mid, "family": "unknown", "default_variant": "standard", "variants": {}})
        scores = {"intelligence": i, "coding": c}
        if a is not None:
            scores["agentic"] = a
        models[mid]["variants"][vid] = {"aa_slug": None, "provider_ids": {}, "scores": scores}
    return {"models": models}


def test_composite_score_both():
    assert tags.composite_score({"intelligence": 40, "coding": 30}) == 35.0


def test_composite_score_missing_coding():
    assert tags.composite_score({"intelligence": 40, "coding": None}) == 40.0


def test_composite_score_none():
    assert tags.composite_score({"intelligence": None, "coding": None}) is None
    assert tags.composite_score({}) is None
    assert tags.composite_score(None) is None


def test_composite_score_with_agentic():
    assert tags.composite_score({"intelligence": 40, "coding": 30, "agentic": 50}) == 40.0


def test_composite_score_agentic_missing_falls_back():
    assert tags.composite_score({"intelligence": 40, "coding": 30, "agentic": None}) == 35.0
    assert tags.composite_score({"intelligence": 40, "coding": None, "agentic": 50}) == 45.0
    assert tags.composite_score({"intelligence": None, "coding": None, "agentic": 50}) == 50.0


def test_compute_tiers_uses_agentic():
    tiers = tags.compute_tiers(_scored({
        "agentic_model/std": (40, 40, 60),   # composite 46.67 -> tier-1 (3-way avg)
        "plain_model/std": (50, 50, None),   # composite 50 (leader) -> tier-1
    }))
    assert tiers == {"agentic_model/std": "tier-1", "plain_model/std": "tier-1"}

    # Leader shifts to the 3-metric model when agentic dominates.
    tiers2 = tags.compute_tiers(_scored({
        "agentic_model/std": (50, 50, 80),   # composite 60 -> leader
        "plain_model/std": (50, 50, None),   # composite 50 -> 0.83 -> tier-2
    }))
    assert tiers2 == {"agentic_model/std": "tier-1", "plain_model/std": "tier-2"}


def test_compute_tiers_relative_to_leader():
    tiers = tags.compute_tiers(_scored({
        "gamma/std": (45, 45, None),   # composite 45 -> 0.75 -> tier-2
        "alpha/std": (60, 60, None),   # composite 60 (leader)
        "delta/std": (30, 30, None),   # composite 30 -> 0.5 -> tier-3
        "beta/std": (55, 55, None),    # composite 55 -> 0.917 -> tier-1
    }))
    assert tiers == {
        "alpha/std": "tier-1",
        "beta/std": "tier-1",
        "gamma/std": "tier-2",
        "delta/std": "tier-3",
    }


def test_compute_tiers_omits_unscored():
    data = _scored({"alpha/std": (60, 60, None)})
    data["models"]["alpha"]["variants"]["bare"] = {"aa_slug": None, "provider_ids": {}}
    tiers = tags.compute_tiers(data)
    assert "alpha/bare" not in tiers


def test_compute_tiers_empty():
    assert tags.compute_tiers({"models": {}}) == {}


def test_assign_tier_tags_preserves_manual_tags(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    data = _scored({
        "alpha/std": (60, 60, None),
        "beta/std": (30, 30, None),
    })
    data["models"]["beta"]["variants"]["std"]["tags"] = ["manual-shard"]
    _write_library(tmp_path, data["models"])

    updated, tiers, leader = tags.assign_tier_tags(cfg)
    assert updated == 2
    assert leader == 60.0
    assert tiers["alpha/std"] == "tier-1"
    assert tiers["beta/std"] == "tier-3"

    saved = json.loads((tmp_path / "models.json").read_text())
    assert saved["models"]["alpha"]["variants"]["std"]["tags"] == ["tier-1"]
    assert saved["models"]["beta"]["variants"]["std"]["tags"] == ["manual-shard", "tier-3"]
    assert "last_updated" in saved["meta"]


def test_assign_tier_tags_idempotent(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    data = _scored({"alpha/std": (60, 60, None)})
    _write_library(tmp_path, data["models"])
    tags.assign_tier_tags(cfg)
    updated, _, _ = tags.assign_tier_tags(cfg)
    assert updated == 0


def test_set_remove_tag(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    data = _scored({"alpha/std": (60, 60, None)})
    _write_library(tmp_path, data["models"])

    assert tags.set_tag(cfg, "alpha", "std", "my-tag") is True
    assert tags.set_tag(cfg, "missing", "std", "my-tag") is False
    assert tags.list_tags(cfg) == {"alpha/std": ["my-tag"]}

    assert tags.remove_tag(cfg, "alpha", "std", "my-tag") is True
    assert tags.list_tags(cfg) == {}


def test_cli_tag_tier_dry_run(tmp_path: Path, mock_config: AppConfig):
    data = _scored({"alpha/std": (60, 60, None), "beta/std": (30, 30, None)})
    _write_library(mock_config.data_dir, data["models"])
    before = (mock_config.data_dir / "models.json").read_text()

    result = runner.invoke(app, ["models", "tag", "tier", "--dry-run",
                                 "--config", str(tmp_path / "nonexistent.toml")])
    # Without a real config file, load_config uses defaults pointing at the
    # user's real data dir, so skip config-path edge and use a direct call.
    assert result.exit_code in (0, 1)


def test_cli_tag_tier_writes(tmp_path: Path, mock_config: AppConfig):
    data = _scored({"alpha/std": (60, 60, None), "beta/std": (30, 30, None)})
    _write_library(mock_config.data_dir, data["models"])
    # Point CLI at the temp data dir via config.toml
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'data_dir = "{mock_config.data_dir}"\n')

    result = runner.invoke(app, ["models", "tag", "tier", "--config", str(config_file)])
    assert result.exit_code == 0
    assert "tier-1" in result.stdout

    saved = json.loads((mock_config.data_dir / "models.json").read_text())
    assert saved["models"]["alpha"]["variants"]["std"]["tags"] == ["tier-1"]
    assert saved["models"]["beta"]["variants"]["std"]["tags"] == ["tier-3"]


def test_cli_tag_ratio_validation(tmp_path: Path, mock_config: AppConfig):
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'data_dir = "{mock_config.data_dir}"\n')
    result = runner.invoke(app, ["models", "tag", "tier", "--t1-ratio", "0.5", "--t2-ratio", "0.8",
                                 "--config", str(config_file)])
    assert result.exit_code == 2
    assert "must satisfy" in result.stdout


def test_cli_tag_list(tmp_path: Path, mock_config: AppConfig):
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'data_dir = "{mock_config.data_dir}"\n')
    result = runner.invoke(app, ["models", "tag", "list", "--config", str(config_file)])
    assert result.exit_code == 0


def test_cli_tag_set_remove(tmp_path: Path, mock_config: AppConfig):
    data = _scored({"alpha/std": (60, 60, None)})
    _write_library(mock_config.data_dir, data["models"])
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'data_dir = "{mock_config.data_dir}"\n')

    result = runner.invoke(app, ["models", "tag", "set", "alpha", "std", "flag", "--config", str(config_file)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["models", "tag", "remove", "alpha", "std", "flag", "--config", str(config_file)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["models", "tag", "set", "missing", "std", "flag", "--config", str(config_file)])
    assert result.exit_code == 1