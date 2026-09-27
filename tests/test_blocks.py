"""Tests for the availability ledger (blocks) and its policy."""
from __future__ import annotations

import json
from pathlib import Path

from model_manager.config import AppConfig, ProviderConfig
from model_manager.domain import blocks


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        providers={"nvidia": ProviderConfig(keys=["K"], litellm_prefix="nvidia_nim")},
    )


def _write_models(tmp_path: Path, pids: list[str]) -> None:
    (tmp_path / "models.json").write_text(json.dumps({
        "meta": {}, "models": {
            "m": {"display_name": "M", "family": "x", "default_variant": "standard",
                  "variants": {"standard": {
                      "aa_slug": None,
                      "provider_ids": {"nvidia": {pid: {} for pid in pids}},
                      "include_in_litellm": True, "scores": {}, "tags": []}}},
        },
    }))


def test_signal_policy_defaults_and_tuning(tmp_path: Path):
    cfg = _cfg(tmp_path)
    for sig in ["unauthorized", "401", "not_found", "404", "gone", "410",
                "unsupported", "payment_required", "402", "forbidden", "403"]:
        assert blocks.is_block_signal(sig, cfg), sig
    for sig in ["dead", "ratelimited", "429", "down", "500", "timeout", "empty", "unknown", "good"]:
        assert not blocks.is_block_signal(sig, cfg), sig
    # user-tunable: removing a signal re-admits it
    cfg.blocking.block_signals = ["unauthorized"]
    assert blocks.is_block_signal("Unauthorized", cfg)
    assert not blocks.is_block_signal("404", cfg)


def test_block_and_release_cycle(tmp_path: Path):
    cfg = _cfg(tmp_path)
    keys = [blocks.provider_key("nvidia", "a/b")]
    assert blocks.is_blocked(blocks.load_ledger(cfg), keys) is None
    blocks.record_observation(cfg, keys, blocked=True, reason="410", code="410", source="proxy")
    entry = blocks.is_blocked(blocks.load_ledger(cfg), keys)
    assert entry and entry["reason"] == "410" and entry["source"] == "proxy"
    assert entry["blocked_since"]
    blocks.record_observation(cfg, keys, blocked=False, reason="")
    assert blocks.is_blocked(blocks.load_ledger(cfg), keys) is None


def test_corrupt_ledger_reads_empty(tmp_path: Path):
    cfg = _cfg(tmp_path)
    (tmp_path / "model_blocks.json").write_text("not json{{{")
    assert blocks.load_ledger(cfg) == {}
    assert blocks.blocked_set(cfg) == set()


def test_fetch_absence_blocks_and_return_releases(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path, ["gone-1", "back-1"])
    res = blocks.record_fetch_observations(cfg, "nvidia", {"back-1"})
    assert res == {"blocked": 1, "released": 0}
    assert blocks.is_blocked(blocks.load_ledger(cfg), [blocks.provider_key("nvidia", "gone-1")])
    # derived litellm key blocked too
    assert f"litellm:nvidia_nim/gone-1" in blocks.blocked_set(cfg)
    res = blocks.record_fetch_observations(cfg, "nvidia", {"gone-1", "back-1"})
    assert res["released"] == 2  # provider + derived keys
    assert blocks.blocked_set(cfg) == set()


def test_empty_fetch_records_nothing(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write_models(tmp_path, ["a-1"])
    assert blocks.record_fetch_observations(cfg, "nvidia", set()) == {"blocked": 0, "released": 0}
    assert blocks.blocked_set(cfg) == set()


def test_assessment_mapping(tmp_path: Path):
    cfg = _cfg(tmp_path)
    res = blocks.record_assessment_observations(
        cfg, "nvidia", {"a": "Unauthorized", "b": "Good", "c": "Dead", "d": "Unknown"}
    )
    assert res == {"blocked": 1, "released": 1}
    assert blocks.provider_key("nvidia", "a") in blocks.blocked_set(cfg)
    assert blocks.provider_key("nvidia", "c") not in blocks.blocked_set(cfg)


def test_probe_mapping_with_alias_keys(tmp_path: Path):
    cfg = _cfg(tmp_path)
    recs = [
        {"model": "tier1", "kind": "alias", "status": "gone", "code": "410"},
        {"model": "m1", "kind": "model", "status": "up", "code": "200"},
        {"model": "m2", "kind": "model", "status": "down", "code": "500"},
    ]
    res = blocks.record_probe_observations(cfg, recs)
    assert res == {"blocked": 1, "released": 1}  # per-target counts
    assert "alias:tier1" in blocks.blocked_set(cfg)
    assert "litellm:tier1" in blocks.blocked_set(cfg)
    assert "litellm:m2" not in blocks.blocked_set(cfg)


def test_blocked_summary_sorted(tmp_path: Path):
    cfg = _cfg(tmp_path)
    blocks.record_observation(cfg, ["b-key"], blocked=True, reason="r", source="s")
    blocks.record_observation(cfg, ["a-key"], blocked=True, reason="r", source="s")
    keys = [e["key"] for e in blocks.blocked_summary(cfg)]
    assert keys == ["a-key", "b-key"]
