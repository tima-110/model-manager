"""Tests for merged LiteLLM router_settings generation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from model_manager.config import AppConfig, ProviderConfig
from model_manager.domain import router_settings as rs
from model_manager.cli.litellm import generate_app

runner = CliRunner()


def _provider(prefix: str) -> ProviderConfig:
    return ProviderConfig(keys=["K1"], litellm_prefix=prefix)


def _cfg(tmp_path: Path, **kwargs) -> AppConfig:
    defaults = dict(
        data_dir=tmp_path,
        providers={
            "nvidia": _provider("nvidia_nim"),
            "gemini": _provider("gemini"),
        },
        litellm_fallbacks_path=(tmp_path / "fallbacks.yaml"),
        litellm_aliases_path=(tmp_path / "aliases.yaml"),
        litellm_router_settings_stub_path=(tmp_path / "stub.yaml"),
        litellm_router_settings_path=(tmp_path / "out.yaml"),
    )
    defaults.update(kwargs)
    return AppConfig(**defaults)


def _variant(pid_map: dict[str, dict[str, dict]], scores: dict, tag: str) -> dict:
    provider_ids = {
        prov: {
            pid: {"assessment": d["assessment"], "availability": d.get("availability", 0.0)}
            for pid, d in pids.items()
        }
        for prov, pids in pid_map.items()
    }
    return {
        "aa_slug": None,
        "provider_ids": provider_ids,
        "include_in_litellm": True,
        "scores": scores,
        "tags": [tag],
    }


def _model(display: str, variant: dict) -> dict:
    return {
        "display_name": display,
        "family": "x",
        "default_variant": "standard",
        "variants": {"standard": variant},
    }


def _write_models(tmp_path: Path) -> None:
    (tmp_path / "models.json").write_text(json.dumps({"meta": {}, "models": {
        "a": _model("A", _variant(
            {"nvidia": {"org/a-1": {"assessment": "Active"}}},
            {"intelligence": 90, "coding": 90}, "tier-1")),
        "b": _model("B", _variant(
            {"nvidia": {"org/b-1": {"assessment": "Active"}}},
            {"intelligence": 85, "coding": 85}, "tier-1")),
    }}, indent=2))


def _write_stub(path: Path, router_block: dict) -> None:
    path.write_text(yaml.safe_dump({"router_settings": router_block}))


def test_regenerate_merges_aliases_and_fallbacks(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(tmp_path / "stub.yaml", {
        "routing_strategy": "simple-shuffle",
        "num_retries": 3,
        "model_group_alias": {"coder": "nvidia_nim/other", "tier1": "stale"},
    })
    doc = rs.build_merged_router_settings(cfg)
    block = doc["router_settings"]
    assert block["routing_strategy"] == "simple-shuffle"
    assert block["num_retries"] == 3
    # manual alias preserved, generated tier1 wins
    assert block["model_group_alias"]["coder"] == "nvidia_nim/other"
    assert block["model_group_alias"]["tier1"] == "nvidia_nim/a-1"
    subjects = {next(iter(m)) for m in block["fallbacks"]}
    assert "nvidia_nim/a-1" in subjects
    assert "nvidia_nim/b-1" in subjects


def test_stub_fallbacks_preserved_and_overwritten_per_subject(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(tmp_path / "stub.yaml", {
        "fallbacks": [{"manual-model": ["x"]}, {"nvidia_nim/a-1": ["stale"]}],
        "model_group_alias": {},
    })
    doc = rs.build_merged_router_settings(cfg)
    by_key = {next(iter(m)): list(m.values())[0] for m in doc["router_settings"]["fallbacks"]}
    assert by_key["manual-model"] == ["x"]
    # generated entry for a-1 replaces the stale stub entry
    assert by_key["nvidia_nim/a-1"] != ["stale"]
    assert "nvidia_nim/b-1" in by_key


def test_missing_router_block_created(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    (tmp_path / "stub.yaml").write_text(yaml.safe_dump({"other": 1}))
    doc = rs.build_merged_router_settings(cfg)
    assert "fallbacks" in doc["router_settings"]
    assert "model_group_alias" in doc["router_settings"]


def test_non_dict_router_block_errors(tmp_path: Path):
    cfg = _cfg(tmp_path)
    (tmp_path / "stub.yaml").write_text("router_settings: [oops]\n")
    with pytest.raises(RuntimeError, match="must be a mapping"):
        rs.build_merged_router_settings(cfg)


def test_from_files_accepts_both_shapes(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # fallbacks unwrapped, aliases wrapped — mirrors live /etc files
    (tmp_path / "fallbacks.yaml").write_text(
        yaml.safe_dump({"fallbacks": [{"m1": ["m2"]}]}))
    (tmp_path / "aliases.yaml").write_text(
        yaml.safe_dump({"router_settings": {"model_group_alias": {"tier1": "m2"}}}))
    _write_stub(tmp_path / "stub.yaml", {
        "routing_strategy": "simple-shuffle",
        "model_group_alias": {"coder": "keep-me"},
    })
    doc = rs.build_merged_router_settings(cfg, from_files=True)
    block = doc["router_settings"]
    assert block["fallbacks"] == [{"m1": ["m2"]}]
    assert block["model_group_alias"] == {"coder": "keep-me", "tier1": "m2"}


def test_from_files_missing_keys_errors(tmp_path: Path):
    cfg = _cfg(tmp_path)
    (tmp_path / "fallbacks.yaml").write_text(yaml.safe_dump({"nope": 1}))
    (tmp_path / "aliases.yaml").write_text(yaml.safe_dump({"model_group_alias": {}}))
    _write_stub(tmp_path / "stub.yaml", {})
    with pytest.raises(RuntimeError, match="No 'fallbacks'"):
        rs.build_merged_router_settings(cfg, from_files=True)


def test_refuses_to_overwrite_stub(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(tmp_path / "stub.yaml", {})
    with pytest.raises(RuntimeError, match="Refusing to overwrite the stub"):
        rs.generate_router_settings_yaml(
            cfg, output_path=(tmp_path / "stub.yaml"))


def test_write_and_backup(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(tmp_path / "stub.yaml", {"model_group_alias": {}})
    out = tmp_path / "out.yaml"
    out.write_text("old")
    rs.generate_router_settings_yaml(cfg)
    assert (tmp_path / "out.yaml.bak").read_text() == "old"
    doc = yaml.safe_load(out.read_text())
    assert "router_settings" in doc


def test_dry_run_shape(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(tmp_path / "stub.yaml", {"model_group_alias": {}})
    text = rs.generate_router_settings_yaml(cfg, dry_run=True)
    assert text is not None
    doc = yaml.safe_load(text)
    assert set(doc.keys()) == {"router_settings"}
    assert "fallbacks" in doc["router_settings"]
    assert "model_group_alias" in doc["router_settings"]


def test_cli_dry_run(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path)
    _write_stub(cfg.litellm_router_settings_stub_path, {"model_group_alias": {}})
    config_file = tmp_path / "config.toml"
    providers_toml = "\n".join(
        f'[providers.{name}]\nkeys=["K1"]\nlitellm_prefix="{pc.litellm_prefix}"'
        for name, pc in cfg.providers.items()
    )
    config_file.write_text(
        f'data_dir = "{cfg.data_dir}"\n'
        f'litellm_router_settings_stub_path = "{cfg.litellm_router_settings_stub_path}"\n'
        f'litellm_router_settings_path = "{cfg.litellm_router_settings_path}"\n'
        f'{providers_toml}\n'
    )
    result = runner.invoke(
        generate_app,
        ["router_settings", "--dry-run", "--config", str(config_file)],
    )
    assert result.exit_code == 0, result.output
    assert "router_settings:" in result.output
    assert "model_group_alias:" in result.output


def test_config_defaults_use_hyphenated_filenames(tmp_path: Path):
    cfg = AppConfig()
    assert cfg.litellm_router_settings_stub_path.name == "litellm-router_settings-stub.yaml"
    assert cfg.litellm_router_settings_path.name == "litellm-router_settings.yaml"
