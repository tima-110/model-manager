"""Tests for LiteLLM proxy scanning (domain + CLI)."""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig
from model_manager.domain import litellm_scan as scan_mod

runner = CliRunner()


def _sse_response(chunks: list[str], code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.getcode.return_value = code
    resp.__enter__.return_value = resp
    resp.__iter__.return_value = iter([(c + "\n").encode() for c in chunks])
    return resp


SSE_OK = [
    'data: {"choices": [{"delta": {"role": "assistant"}}]}',
    'data: {"choices": [{"delta": {"content": "1"}}]}',
    'data: {"choices": [{"delta": {"content": "2"}}]}',
    'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}',
    "data: [DONE]",
]


def _fixture_config(tmp_path: Path) -> AppConfig:
    litellm_yaml = tmp_path / "litellm.yaml"
    litellm_yaml.write_text("include:\n  - prov.yaml\n")
    (tmp_path / "prov.yaml").write_text(
        "model_list:\n"
        "  - model_name: alpha\n"
        "    litellm_params: {model: x/alpha}\n"
        "  - model_name: beta\n"
        "    litellm_params: {model: x/beta}\n"
    )
    rs_path = tmp_path / "rs.yaml"
    rs_path.write_text(
        "router_settings:\n  model_group_alias:\n    tier1: alpha\n    myalias: beta\n"
    )
    return AppConfig(
        data_dir=tmp_path,
        litellm_config_path=litellm_yaml,
        litellm_router_settings_path=rs_path,
        litellm_aliases_path=tmp_path / "aliases.yaml",
    )


def test_enumerate_targets_models_and_all_aliases(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    targets = scan_mod.enumerate_targets(cfg)
    assert targets["models"] == ["alpha", "beta"]
    # ALL aliases from the merged router_settings file, not just tiers
    assert targets["aliases"] == ["tier1", "myalias"]
    assert targets["alias_targets"] == {"tier1": "alpha", "myalias": "beta"}


def test_enumerate_targets_falls_back_to_aliases_file(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    cfg.litellm_router_settings_path.unlink()
    (tmp_path / "aliases.yaml").write_text("model_group_alias:\n  tier2: beta\n")
    targets = scan_mod.enumerate_targets(cfg)
    assert targets["aliases"] == ["tier2"]


def test_enumerate_targets_missing_config():
    cfg = AppConfig(litellm_config_path=Path("/nonexistent/litellm.yaml"))
    try:
        scan_mod.enumerate_targets(cfg)
    except RuntimeError as e:
        assert "YAML file not found" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_reconcile_targets():
    drift = scan_mod.reconcile_targets(
        {"models": ["a", "b"], "aliases": ["t1"]}, ["a", "zzz"]
    )
    assert drift["unserved"] == ["b", "t1"]
    assert drift["extra_served"] == ["zzz"]
    assert drift["target_count"] == 3


def test_probe_success_metrics():
    with patch("urllib.request.urlopen", return_value=_sse_response(SSE_OK)):
        rec = scan_mod.probe_litellm_model("http://x", "key", "alpha", max_tokens=64)
    assert rec["status"] == "up"
    assert rec["code"] == "200"
    assert rec["requested_tokens"] == 64
    assert rec["prompt_tokens"] == 10
    assert rec["completion_tokens"] == 2
    assert rec["ttft_ms"] is not None and rec["ttft_ms"] >= 0
    assert rec["tps"] is not None and rec["tps"] > 0
    assert rec["tpm_est"] == round(rec["tps"] * 60, 1)


def test_probe_unauthorized():
    err = urllib.error.HTTPError(
        "http://x", 401, "Unauthorized", {}, None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        rec = scan_mod.probe_litellm_model("http://x", "key", "alpha")
    assert rec["status"] == "unauthorized"
    assert rec["code"] == "401"


def test_probe_not_found():
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        rec = scan_mod.probe_litellm_model("http://x", "key", "ghost")
    assert rec["status"] == "not_found"


def test_probe_timeout():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        rec = scan_mod.probe_litellm_model("http://x", "key", "slow")
    assert rec["status"] == "timeout"


def test_probe_missing_key_raises():
    try:
        scan_mod.probe_litellm_model("http://x", "", "alpha")
    except RuntimeError as e:
        assert "LITELLM_MASTER_KEY" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_probe_reasoning_only_is_up_with_counts():
    chunks = [
        'data: {"choices": [{"delta": {"reasoning_content": "thinking"}}]}',
        'data: {"choices": [{"delta": {"reasoning_content": "more"}, "finish_reason": "stop"}]}',
        'data: {"usage": {"prompt_tokens": 8, "completion_tokens": 0}}',
        "data: [DONE]",
    ]
    with patch("urllib.request.urlopen", return_value=_sse_response(chunks)):
        rec = scan_mod.probe_litellm_model("http://x", "key", "thinker", kind="alias")
    assert rec["status"] == "up"
    assert rec["kind"] == "alias"
    assert rec["reasoning_chunks"] == 2
    assert rec["reasoning_chars"] == len("thinkingmore")
    assert rec["finish_reason"] == "stop"
    assert rec["tps"] is None  # no content tokens to rate


def test_probe_empty_200_distinct_status():
    chunks = [
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',
        "data: [DONE]",
    ]
    with patch("urllib.request.urlopen", return_value=_sse_response(chunks)):
        rec = scan_mod.probe_litellm_model("http://x", "key", "quiet")
    assert rec["status"] == "empty"
    assert rec["code"] == "200"
    assert rec["reasoning_chunks"] == 0
    assert rec["attempts"] == 2  # retried once with headroom, still empty


def test_probe_empty_retries_to_success():
    empty_chunks = [
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',
        "data: [DONE]",
    ]
    with patch("urllib.request.urlopen", side_effect=[
        _sse_response(empty_chunks),
        _sse_response(SSE_OK),
    ]) as mock_urlopen:
        rec = scan_mod.probe_litellm_model("http://x", "key", "flaky", max_tokens=64)
    assert mock_urlopen.call_count == 2
    assert rec["status"] == "up"
    assert rec["attempts"] == 2
    assert rec["requested_tokens"] == 256  # retry uses 4x headroom


def test_scan_targets_passes_kinds():
    seen: dict[str, str] = {}
    real_probe = scan_mod.probe_litellm_model

    def spy(base_url, api_key, model, **kwargs):
        seen[model] = kwargs.get("kind", "model")
        return {"model": model, "status": "up"}

    with patch.object(scan_mod, "probe_litellm_model", side_effect=spy):
        scan_mod.scan_targets("http://x", "k", ["a", "t1"], kinds={"t1": "alias"})
    assert seen == {"a": "model", "t1": "alias"}


def test_save_litellm_scan(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    records = [
        {"model": "a", "status": "up", "ttft_ms": 100.0, "tps": 20.0,
         "tpm_est": 1200.0, "code": "200", "timestamp": "t"},
        {"model": "b", "status": "down", "ttft_ms": None, "tps": None,
         "tpm_est": None, "code": "000", "timestamp": "t"},
    ]
    path = scan_mod.save_litellm_scan(cfg, records)
    doc = json.loads(path.read_text())
    assert doc["metadata"]["scanned"] == 2
    assert doc["metadata"]["up"] == 1
    assert doc["metadata"]["avg_ttft_ms"] == 100.0


def test_cli_scan_requires_key(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{tmp_path}"\n')
    with patch("model_manager.domain.auth.get_secret", return_value=None):
        result = runner.invoke(app, ["litellm", "scan", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "LITELLM_MASTER_KEY" in result.stdout


def test_cli_scan_dry_run(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'data_dir = "{tmp_path}"\n'
        f'litellm_config_path = "{cfg.litellm_config_path}"\n'
        f'litellm_router_settings_path = "{cfg.litellm_router_settings_path}"\n'
        f'litellm_aliases_path = "{tmp_path / "aliases.yaml"}"\n'
    )
    with patch("model_manager.domain.auth.get_secret", return_value="k"), \
         patch(
             "model_manager.domain.litellm_scan.fetch_proxy_models",
             return_value=["alpha"],
         ):
        result = runner.invoke(
            app, ["litellm", "scan", "--config", str(cfg_file), "--dry-run"]
        )
    assert result.exit_code == 0
    assert "alpha" in result.stdout and "beta" in result.stdout
    assert "myalias" in result.stdout
    # drift warning names the unserved target
    assert "beta" in result.stdout


def test_cli_scan_writes_file(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{tmp_path}"\n')
    fake_targets = {"models": ["alpha"], "aliases": []}
    fake_records = [{
        "model": "alpha", "status": "up", "ttft_ms": 50.0, "tps": 10.0,
        "tpm_est": 600.0, "code": "200", "timestamp": "t",
    }]
    with patch("model_manager.domain.auth.get_secret", return_value="k"), \
         patch("model_manager.domain.litellm_scan.enumerate_targets", return_value=fake_targets), \
         patch("model_manager.domain.litellm_scan.fetch_proxy_models", return_value=["alpha"]), \
         patch("model_manager.domain.litellm_scan.scan_targets", return_value=fake_records):
        result = runner.invoke(app, ["litellm", "scan", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert (tmp_path / "litellm_scan.json").exists()
    assert "alpha" in result.stdout


def test_dashboard_renders_proxy_scan_table(tmp_path: Path):
    import json as _json

    from model_manager.dashboard import generate_dashboard

    cfg = AppConfig(data_dir=tmp_path)
    (tmp_path / "litellm_scan.json").write_text(_json.dumps({
        "metadata": {"timestamp": "2026-01-01T00:00:00Z", "scanned": 1, "up": 1},
        "models": [{
            "model": "tier1", "status": "up", "ttft_ms": 120.0, "tps": 15.5,
            "tpm_est": 930.0, "code": "200", "timestamp": "2026-01-01T00:00:00Z",
        }],
    }))
    html = generate_dashboard(cfg).read_text()
    assert "LiteLLM Proxy Scan" in html
    assert "tier1" in html
    assert ">930<" in html


def test_dashboard_no_scan_placeholder(tmp_path: Path):
    from model_manager.dashboard import generate_dashboard

    cfg = AppConfig(data_dir=tmp_path)
    html = generate_dashboard(cfg).read_text()
    assert "LiteLLM Proxy Scan" in html
    assert "litellm scan" in html
