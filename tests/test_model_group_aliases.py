"""Tests for LiteLLM model_group_alias generation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from model_manager.config import (
    AppConfig,
    ProviderConfig,
    TierAliasConfig,
    TierProvidersConfig,
    load_config,
)
from model_manager.domain import model_group_aliases

from model_manager.cli.litellm import generate_app

runner = CliRunner()


def _provider(prefix: str) -> ProviderConfig:
    return ProviderConfig(keys=["K1"], litellm_prefix=prefix)


def _cfg(tmp_path: Path, **kwargs) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        providers={
            "nvidia": _provider("nvidia_nim"),
            "gemini": _provider("gemini"),
            "openrouter": _provider("openrouter"),
        },
        litellm_aliases_path=(tmp_path / "out.yaml"),
        **kwargs,
    )


def _variant(
    assessment_by_pid: dict[str, dict[str, dict]],
    scores: dict,
    tag: str | None = "tier-2",
    **extra,
) -> dict:
    provider_ids: dict = {}
    for prov, pid_map in assessment_by_pid.items():
        provider_ids[prov] = {
            pid: {"assessment": d["assessment"], "availability": d.get("availability", 0.0)}
            for pid, d in pid_map.items()
        }
    variant: dict = {
        "aa_slug": None,
        "provider_ids": provider_ids,
        "include_in_litellm": True,
        "scores": scores,
    }
    if tag is not None:
        variant["tags"] = [tag]
    variant.update(extra)
    return variant


def _write(data_dir: Path, models: dict) -> None:
    (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": models}, indent=2))


def _model(display: str, variant: dict, variant_name: str = "standard") -> dict:
    return {
        "display_name": display,
        "family": "x",
        "default_variant": variant_name,
        "variants": {variant_name: variant},
    }


def _library(tmp_path: Path) -> AppConfig:
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "minimax-m3": _model("Minimax M3", _variant(
            {"nvidia": {"minimaxai/minimax-m3": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-1")),
        "deepseek-v4": _model("DeepSeek V4", _variant(
            {"nvidia": {"deepseek-ai/deepseek-v4-flash": {"assessment": "Active"}}},
            {"intelligence": 80, "coding": 80}, tag="tier-2")),
        "gemma-4": _model("Gemma 4", _variant(
            {"gemini": {"gemma-4-31b-it": {"assessment": "Active"}}},
            {"intelligence": 60, "coding": 60}, tag="tier-3")),
    })
    return cfg


def test_nvidia_preferred_over_higher_composite(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "gemini-leader": _model("Gemini Leader", _variant(
            {"gemini": {"gemini-leader-1": {"assessment": "Active"}}},
            {"intelligence": 95, "coding": 95}, tag="tier-1")),
        "nvidia-runner": _model("Nvidia Runner", _variant(
            {"nvidia": {"org/nvidia-runner-1": {"assessment": "Active"}}},
            {"intelligence": 86, "coding": 86}, tag="tier-1")),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result["tier1"] == "nvidia_nim/nvidia-runner-1"


def test_tier_purity_beats_provider_preference(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "tier1-gemini": _model("Tier1 Gemini", _variant(
            {"gemini": {"tier1-gemini-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-1")),
        "tier2-nvidia": _model("Tier2 Nvidia", _variant(
            {"nvidia": {"org/tier2-nvidia-1": {"assessment": "Active"}}},
            {"intelligence": 75, "coding": 75}, tag="tier-2")),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result["tier1"] == "gemini/tier1-gemini-1"
    assert result["tier2"] == "nvidia_nim/tier2-nvidia-1"


def test_fallback_to_other_provider_without_nvidia(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "gemma": _model("Gemma", _variant(
            {"gemini": {"gemma-4-31b-it": {"assessment": "Active"}}},
            {"intelligence": 60, "coding": 60}, tag="tier-3")),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result == {"tier3": "gemini/gemma-4-31b-it"}


def test_custom_provider_order(tmp_path: Path):
    cfg = _cfg(tmp_path, tier_providers=TierProvidersConfig(
        tier1=TierAliasConfig(provider_order=["gemini", "nvidia"]),
    ))
    _write(tmp_path, {
        "gemini-leader": _model("Gemini Leader", _variant(
            {"gemini": {"gemini-leader-1": {"assessment": "Active"}}},
            {"intelligence": 95, "coding": 95}, tag="tier-1")),
        "nvidia-runner": _model("Nvidia Runner", _variant(
            {"nvidia": {"org/nvidia-runner-1": {"assessment": "Active"}}},
            {"intelligence": 86, "coding": 86}, tag="tier-1")),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result["tier1"] == "gemini/gemini-leader-1"


def test_unknown_providers_ignored(tmp_path: Path):
    cfg = _cfg(tmp_path, tier_providers=TierProvidersConfig(
        tier1=TierAliasConfig(provider_order=["bogus", "nvidia"]),
    ))
    _write(tmp_path, {
        "m": _model("M", _variant(
            {"nvidia": {"org/m-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-1")),
    })
    assert model_group_aliases.build_alias_map(cfg)["tier1"] == "nvidia_nim/m-1"


def test_exclusions(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "unauth": _model("Unauth", _variant(
            {"nvidia": {"org/unauth-1": {"assessment": "Unauthorized"}}},
            {"intelligence": 99, "coding": 99}, tag="tier-1")),
        "excluded": _model("Excluded", _variant(
            {"nvidia": {"org/excluded-1": {"assessment": "Active"}}},
            {"intelligence": 98, "coding": 98}, tag="tier-1",
            include_in_litellm=False)),
        "unscored": _model("Unscored", _variant(
            {"nvidia": {"org/unscored-1": {"assessment": "Active"}}},
            {}, tag="tier-1")),
        "good": _model("Good", _variant(
            {"nvidia": {"org/good-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-1")),
    })
    assert model_group_aliases.build_alias_map(cfg)["tier1"] == "nvidia_nim/good-1"


def test_scan_unauthorized_excluded(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "m": _model("M", _variant(
            {"nvidia": {"org/m-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-1")),
    })
    (tmp_path / "nvidia_scan.json").write_text(json.dumps({
        "models": {"org/m-1": {"summary": {"assessment": "unauthorized"}}},
    }))
    with pytest.raises(RuntimeError):
        model_group_aliases.generate_aliases_yaml(cfg, dry_run=True)


def test_missing_tags_computed_from_scores(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "leader": _model("Leader", _variant(
            {"nvidia": {"org/leader-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag=None)),
        "laggard": _model("Laggard", _variant(
            {"gemini": {"laggard-1": {"assessment": "Active"}}},
            {"intelligence": 50, "coding": 50}, tag=None)),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result["tier1"] == "nvidia_nim/leader-1"
    assert result["tier3"] == "gemini/laggard-1"
    assert "tier2" not in result


def test_empty_tier_omitted_and_empty_raises(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "m": _model("M", _variant(
            {"nvidia": {"org/m-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, tag="tier-2")),
    })
    result = model_group_aliases.build_alias_map(cfg)
    assert result == {"tier2": "nvidia_nim/m-1"}

    _write(tmp_path, {})
    with pytest.raises(RuntimeError):
        model_group_aliases.generate_aliases_yaml(cfg, dry_run=True)


def test_dry_run_yaml_shape(tmp_path: Path):
    cfg = _library(tmp_path)
    text = model_group_aliases.generate_aliases_yaml(cfg, dry_run=True)
    doc = yaml.safe_load(text)
    assert set(doc.keys()) == {"model_group_alias"}
    assert set(doc["model_group_alias"].keys()) == {"tier1", "tier2", "tier3"}
    assert "hermes-default" not in doc["model_group_alias"]
    assert doc["model_group_alias"]["tier1"] == "nvidia_nim/minimax-m3"
    assert doc["model_group_alias"]["tier2"] == "nvidia_nim/deepseek-v4-flash"
    assert doc["model_group_alias"]["tier3"] == "gemini/gemma-4-31b-it"


def test_write_and_backup(tmp_path: Path):
    cfg = _library(tmp_path)
    out = cfg.litellm_aliases_path
    out.write_text("old")
    model_group_aliases.generate_aliases_yaml(cfg)
    assert out.exists()
    assert (out.with_suffix(out.suffix + ".bak")).read_text() == "old"
    doc = yaml.safe_load(out.read_text())
    assert "model_group_alias" in doc


def test_config_defaults_and_toml(tmp_path: Path):
    cfg = AppConfig()
    assert cfg.tier_providers.tier1.provider_order == []
    assert cfg.litellm_aliases_path.name == "litellm-aliases.yaml"

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'litellm_aliases_path = "/tmp/aliases.yaml"\n'
        '[tier_providers.tier1]\nprovider_order = ["nvidia", "gemini"]\n'
    )
    loaded = load_config(cfg_file)
    assert loaded.tier_providers.tier1.provider_order == ["nvidia", "gemini"]
    assert loaded.tier_providers.tier2.provider_order == []


def test_cli_generate_aliases_dry_run(tmp_path: Path):
    cfg = _library(tmp_path)
    config_file = tmp_path / "config.toml"
    providers_toml = "\n".join(
        f'[providers.{name}]\nkeys=["K1"]\nlitellm_prefix="{pc.litellm_prefix}"'
        for name, pc in cfg.providers.items()
    )
    config_file.write_text(
        f'data_dir = "{cfg.data_dir}"\n'
        f'litellm_aliases_path = "{cfg.litellm_aliases_path}"\n'
        f'{providers_toml}\n'
        '[tier_providers.tier1]\nprovider_order = ["nvidia", "gemini"]\n'
    )
    result = runner.invoke(
        generate_app,
        ["aliases", "--dry-run", "--config", str(config_file)],
    )
    assert result.exit_code == 0, result.output
    assert "model_group_alias:" in result.output
    assert "nvidia_nim/minimax-m3" in result.output
