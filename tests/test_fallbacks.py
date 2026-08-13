"""Tests for LiteLLM fallback generation."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from model_manager.config import AppConfig, ProviderConfig
from model_manager.domain import fallbacks

from model_manager.cli.litellm import generate_app

runner = CliRunner()


def _provider(prefix: str) -> ProviderConfig:
    return ProviderConfig(keys=["K1"], litellm_prefix=prefix)


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        providers={
            "nvidia": _provider("nvidia_nim"),
            "ollama": _provider("ollama"),
        },
        litellm_fallbacks_path=(tmp_path / "out.yaml"),
    )


def _variant(assessment_by_pid: dict[str, dict], scores: dict, tag: str = "tier-2") -> dict:
    provider_ids: dict = {}
    for prov, pid_map in assessment_by_pid.items():
        provider_ids[prov] = {pid: {"assessment": a, "availability": av} for pid, a, av in (
            (pid, d["assessment"], d.get("availability", 0.0)) for pid, d in pid_map.items()
        )}
    return {
        "aa_slug": None,
        "provider_ids": provider_ids,
        "include_in_litellm": True,
        "scores": scores,
        "tags": [tag],
    }


def _write(data_dir: Path, models: dict) -> None:
    (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": models}, indent=2))


def _library(tmp_path: Path) -> AppConfig:
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "deepseek-v4": {
            "display_name": "DeepSeek V4", "family": "x", "default_variant": "standard",
            "variants": {
                "flash": _variant({
                    "nvidia": {"deepseek-ai/deepseek-v4-flash": {"assessment": "Dead", "availability": 0.0}},
                    "ollama": {"deepseek-v4-flash": {"assessment": "Active", "availability": 1.0}},
                }, {"intelligence": 50, "coding": 50}),
            },
        },
        "minimax-m2.7": {
            "display_name": "Minimax M2.7", "family": "x", "default_variant": "standard",
            "variants": {
                "standard": _variant({
                    "nvidia": {"minimaxai/minimax-m2.7": {"assessment": "Dead", "availability": 0.0}},
                    "ollama": {"minimax-m2.7": {"assessment": "Active", "availability": 1.0}},
                }, {"intelligence": 45, "coding": 45}),
            },
        },
        "glm-5.1": {
            "display_name": "GLM 5.1", "family": "x", "default_variant": "standard",
            "variants": {
                "standard": _variant({
                    "nvidia": {"z-ai/glm-5.1": {"assessment": "Dead", "availability": 0.0}},
                    "ollama": {"glm-5.1": {"assessment": "Ratelimited", "availability": 0.0}},
                }, {"intelligence": 40, "coding": 40}),
            },
        },
    })
    return cfg


def test_status_rank_order():
    assert fallbacks.status_rank("active") < fallbacks.status_rank("good")
    assert fallbacks.status_rank("good") < fallbacks.status_rank("ratelimited")
    assert fallbacks.status_rank("ratelimited") < fallbacks.status_rank("slow")
    assert fallbacks.status_rank("slow") < fallbacks.status_rank("weak")
    assert fallbacks.status_rank("weak") < fallbacks.status_rank("dead")
    assert fallbacks.status_rank(None) == fallbacks.UNKNOWN_RANK
    assert fallbacks.status_rank("Active") == fallbacks.status_rank("active")


def test_seg_a_same_variant_then_seg_b_same_tier(tmp_path: Path):
    cfg = _library(tmp_path)
    result = fallbacks.build_fallbacks(cfg)

    by_key = {next(iter(m)): list(m.values())[0] for m in result}
    subj = "nvidia_nim/deepseek-v4-flash"

    assert subj in by_key
    # Active ollama copy of same variant first
    assert by_key[subj][0] == "ollama/deepseek-v4-flash"
    # Then same-tier models, each via best provider (Active > Ratelimited > Dead)
    # minimax best is ollama (Active), glm best is ollama (Ratelimited)
    assert "ollama/minimax-m2.7" in by_key[subj]
    assert "ollama/glm-5.1" in by_key[subj]
    # composite ordering: minimax (45) before glm (40)
    assert by_key[subj].index("ollama/minimax-m2.7") < by_key[subj].index("ollama/glm-5.1")


def test_limit_caps_fallback_list(tmp_path: Path):
    cfg = _library(tmp_path)
    result = fallbacks.build_fallbacks(cfg, limit=1)
    assert all(len(list(m.values())[0]) <= 1 for m in result)


def test_unauthorized_excluded(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "deepseek-v4": {
            "display_name": "DeepSeek V4", "family": "x", "default_variant": "standard",
            "variants": {
                "flash": _variant({
                    "nvidia": {"deepseek-ai/deepseek-v4-flash": {"assessment": "Unauthorized", "availability": 0.0}},
                    "ollama": {"deepseek-v4-flash": {"assessment": "Active", "availability": 1.0}},
                }, {"intelligence": 50, "coding": 50}),
            },
        },
    })
    result = fallbacks.build_fallbacks(cfg)
    assert result == []
    assert not any("nvidia_nim/deepseek-v4-flash" in m for m in result)


def test_dry_run_emits_yaml(tmp_path: Path):
    cfg = _library(tmp_path)
    text = fallbacks.generate_fallbacks_yaml(cfg, dry_run=True)
    doc = yaml.safe_load(text)
    assert "fallbacks" in doc
    subjects = {next(iter(m)) for m in doc["fallbacks"]}
    assert "nvidia_nim/deepseek-v4-flash" in subjects
    assert "ollama/deepseek-v4-flash" in subjects


def test_write_and_backup(tmp_path: Path):
    cfg = _library(tmp_path)
    out = cfg.litellm_fallbacks_path
    out.write_text("old")
    fallbacks.generate_fallbacks_yaml(cfg)
    assert out.exists()
    assert (out.with_suffix(out.suffix + ".bak")).read_text() == "old"
    doc = yaml.safe_load(out.read_text())
    assert "fallbacks" in doc


def test_cli_generate_fallbacks_dry_run(tmp_path: Path):
    cfg = _library(tmp_path)
    config_file = tmp_path / "config.toml"
    providers_toml = "\n".join(
        f'[providers.{name}]\nkeys=["K1"]\nlitellm_prefix="{pc.litellm_prefix}"'
        for name, pc in cfg.providers.items()
    )
    config_file.write_text(
        f'data_dir = "{cfg.data_dir}"\nlitellm_fallbacks_path = "{cfg.litellm_fallbacks_path}"\n{providers_toml}\n'
    )
    result = runner.invoke(
        generate_app,
        ["fallbacks", "--dry-run", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    assert "fallbacks:" in result.output
    assert "nvidia_nim/deepseek-v4-flash" in result.output
