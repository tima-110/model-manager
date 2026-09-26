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


def test_generated_fallbacks_are_dag(tmp_path: Path):
    cfg = _library(tmp_path)
    result = fallbacks.build_fallbacks(cfg)
    assert fallbacks.is_dag(result)
    assert fallbacks.find_cycles(result) == []
    # No symmetric pairs: if A lists B, B must not list A.
    by_key = {next(iter(m)): list(m.values())[0] for m in result}
    for subject, targets in by_key.items():
        for target in targets:
            assert subject not in by_key.get(target, [])


def test_strict_single_key_shape(tmp_path: Path):
    cfg = _library(tmp_path)
    result = fallbacks.build_fallbacks(cfg)
    assert result
    for item in result:
        assert isinstance(item, dict) and len(item) == 1
        targets = next(iter(item.values()))
        assert isinstance(targets, list) and targets
        assert all(isinstance(t, str) for t in targets)


def test_find_cycles_direct_and_indirect():
    assert fallbacks.is_dag([{"a": ["b"]}, {"b": ["c"]}])
    direct = [{"a": ["b"]}, {"b": ["a"]}]
    assert not fallbacks.is_dag(direct)
    assert fallbacks.find_cycles(direct)
    indirect = [{"a": ["b"]}, {"b": ["c"]}, {"c": ["a"]}]
    assert not fallbacks.is_dag(indirect)
    assert fallbacks.find_cycles(indirect)


def test_validate_fallbacks_unknown_names():
    doc = [{"a": ["b", "ghost"]}, {"c": ["a"]}]
    report = fallbacks.validate_fallbacks(doc, {"a", "b", "c"})
    assert report["unknown_names"] == ["ghost"]
    assert report["malformed"] == []
    assert report["cycles"] == []
    bad = [{"a": ["a"]}, {"k1": ["v"], "k2": ["v"]}, ["nope"]]
    report = fallbacks.validate_fallbacks(bad, {"a"})
    assert "a" in report["self_edges"]
    assert report["malformed"]


def test_sanitize_drops_unknown_and_breaks_cycles():
    doc = [
        {"a": ["b", "ghost", "a"]},
        {"b": ["a"]},
        {"ghost": ["a"]},
        {"k1": ["v"], "k2": ["v"]},
    ]
    clean = fallbacks.sanitize_fallbacks(doc, {"a", "b"})
    by_key = {next(iter(m)): list(m.values())[0] for m in clean}
    assert by_key == {"a": ["b"]}
    assert fallbacks.is_dag(clean)


def _free_cfg(tmp_path: Path) -> AppConfig:
    cfg = AppConfig(
        data_dir=tmp_path,
        providers={
            "openrouter": _provider("openrouter"),
            "gemini": _provider("gemini"),
        },
        litellm_fallbacks_path=(tmp_path / "out.yaml"),
    )
    _write(tmp_path, {
        "gemma-4-31b-it": {
            "display_name": "Gemma 4 31B", "family": "x", "default_variant": "standard",
            "variants": {
                "standard": _variant({
                    "openrouter": {
                        "google/gemma-4-31b-it:free": {"assessment": "Active", "availability": 1.0},
                        "google/gemma-4-31b-it": {"assessment": "Good", "availability": 0.9},
                    },
                    "gemini": {"gemma-4-31b-it": {"assessment": "Active", "availability": 1.0}},
                }, {"intelligence": 50, "coding": 50}),
            },
        },
    })
    return cfg


def test_quoting_colon_and_slash_and_valid_names(tmp_path: Path):
    cfg = _free_cfg(tmp_path)
    valid = fallbacks.collect_valid_model_names(cfg)
    assert "openrouter/gemma-4-31b-it:free" in valid
    text = fallbacks.generate_fallbacks_yaml(cfg, dry_run=True)
    assert '"openrouter/gemma-4-31b-it:free"' in text
    assert '"gemini/gemma-4-31b-it"' in text
    doc = yaml.safe_load(text)
    report = fallbacks.validate_fallbacks(doc["fallbacks"], valid)
    assert report["unknown_names"] == []
    assert report["cycles"] == []
    assert fallbacks.is_dag(doc["fallbacks"])


def test_include_in_litellm_flag_never_becomes_model_name(tmp_path: Path):
    """Regression: the sentinel key inside provider maps must not leak."""
    from model_manager.domain.yaml_gen import _iter_provider_ids

    pmap = {
        "org/real-model": {"assessment": "Active", "availability": 1.0},
        "include_in_litellm": True,
    }
    assert _iter_provider_ids(pmap) == ["org/real-model"]

    cfg = _cfg(tmp_path)
    _write(tmp_path, {
        "m": {
            "display_name": "M", "family": "x", "default_variant": "standard",
            "variants": {
                "standard": {
                    "aa_slug": None,
                    "provider_ids": {
                        "nvidia": dict(pmap),
                        "ollama": {"m": {"assessment": "Active", "availability": 1.0}},
                    },
                    "include_in_litellm": True,
                    "scores": {"intelligence": 50, "coding": 50},
                    "tags": ["tier-2"],
                },
            },
        },
    })
    result = fallbacks.build_fallbacks(cfg)
    names = {next(iter(m)) for m in result} | {
        t for m in result for t in next(iter(m.values()))
    }
    assert "nvidia_nim/include_in_litellm" not in names
    assert "nvidia_nim/real-model" in names


def test_reported_deepseek_0731_loop_sanitized_to_cascade():
    """Regression for the live DeepSeek Flash 0731 loop."""
    doc = [
        {"nvidia_nim/deepseek-v4-flash-0731": [
            "openrouter/deepseek-v4-flash:free",
            "ollama/deepseek-v4-flash:0731",
        ]},
        {"openrouter/deepseek-v4-flash:free": [
            "huggingface/DeepSeek-V4-Flash-0731",
            "ollama/deepseek-v4-flash:0731",
        ]},
        {"huggingface/DeepSeek-V4-Flash-0731": ["ollama/deepseek-v4-flash:0731"]},
        {"ollama/deepseek-v4-flash:0731": [
            "openrouter/deepseek-v4-flash:free",
            "nvidia_nim/deepseek-v4-flash-0731",
        ]},
    ]
    assert not fallbacks.is_dag(doc)
    clean = fallbacks.sanitize_fallbacks(doc, None)
    assert fallbacks.is_dag(clean)
    by_key = {next(iter(m)): list(m.values())[0] for m in clean}
    # Linear cascade preserved; back-edges to ancestors pruned.
    assert by_key["nvidia_nim/deepseek-v4-flash-0731"][0] == "openrouter/deepseek-v4-flash:free"
    assert "nvidia_nim/deepseek-v4-flash-0731" not in by_key.get(
        "ollama/deepseek-v4-flash:0731", [])
    assert "openrouter/deepseek-v4-flash:free" not in by_key.get(
        "ollama/deepseek-v4-flash:0731", [])


def test_reported_glm_53_mutual_loop_sanitized():
    """Regression for the live GLM 5.3 A->B->A loop."""
    doc = [
        {"nvidia_nim/glm-5.3": ["ollama/glm-5.3"]},
        {"ollama/glm-5.3": ["nvidia_nim/glm-5.3"]},
    ]
    assert fallbacks.find_cycles(doc) == [
        ["nvidia_nim/glm-5.3", "ollama/glm-5.3", "nvidia_nim/glm-5.3"]
    ]
    clean = fallbacks.sanitize_fallbacks(doc, None)
    assert clean == [{"nvidia_nim/glm-5.3": ["ollama/glm-5.3"]}]


def test_validate_reports_duplicate_subjects():
    doc = [{"a": ["b"]}, {"a": ["c"]}]
    report = fallbacks.validate_fallbacks(doc, {"a", "b", "c"})
    assert report["duplicate_subjects"] == ["a"]


def test_assert_valid_fallbacks_gate():
    import pytest

    fallbacks.assert_valid_fallbacks([{"a": ["b"]}], {"a", "b"})
    with pytest.raises(RuntimeError, match="cycles"):
        fallbacks.assert_valid_fallbacks(
            [{"a": ["b"]}, {"b": ["a"]}], {"a", "b"})
    with pytest.raises(RuntimeError, match="unregistered"):
        fallbacks.assert_valid_fallbacks([{"a": ["ghost"]}], {"a"})
