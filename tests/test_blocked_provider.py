"""Tests for fully ledger-blocked providers: empty model_list, no restart veto."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from model_manager.config import AppConfig, ProviderConfig
from model_manager.domain import blocks, yaml_gen
from model_manager.domain.generate_all import run_generate_all


@pytest.fixture
def blocked_setup(tmp_path: Path) -> AppConfig:
    """One model mapped to a provider; caller blocks the target."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "models.json").write_text(json.dumps({
        "meta": {},
        "models": {
            "m": {
                "display_name": "M", "family": "x", "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": None,
                        "provider_ids": {"hf": {"hf/dead-model": {}}},
                    },
                },
            },
        },
    }))
    out = tmp_path / "litellm-hf.yaml"
    return AppConfig(
        data_dir=data_dir,
        providers={"hf": ProviderConfig(
            keys=["K"], output_path=out, litellm_prefix="hf")},
    )


def _block_all(cfg: AppConfig) -> None:
    blocks.record_observation(
        cfg, [blocks.provider_key("hf", "hf/dead-model")],
        blocked=True, reason="scan assessment: unauthorized", source="scan",
    )


class TestAllTargetsBlocked:
    def test_raises_distinct_error(self, blocked_setup: AppConfig):
        _block_all(blocked_setup)
        with pytest.raises(yaml_gen.AllTargetsBlockedError) as exc:
            yaml_gen.generate_provider_yaml(blocked_setup, "hf", dry_run=True)
        assert exc.value.provider == "hf"
        assert exc.value.blocked == 1

    def test_still_a_runtime_error(self, blocked_setup: AppConfig):
        """Single-provider CLI keeps its behavior via except RuntimeError."""
        _block_all(blocked_setup)
        with pytest.raises(RuntimeError, match="ledger-blocked"):
            yaml_gen.generate_provider_yaml(blocked_setup, "hf", dry_run=True)

    def test_no_mappings_stays_plain_error(self, tmp_path: Path):
        """Genuine misconfiguration (nothing mapped) is still a hard error."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": {}}))
        cfg = AppConfig(
            data_dir=data_dir,
            providers={"hf": ProviderConfig(
                keys=["K"], output_path=tmp_path / "hf.yaml", litellm_prefix="hf")},
        )
        with pytest.raises(RuntimeError) as exc:
            yaml_gen.generate_provider_yaml(cfg, "hf", dry_run=True)
        assert not isinstance(exc.value, yaml_gen.AllTargetsBlockedError)

    def test_generate_all_writes_empty_and_stays_ok(self, blocked_setup: AppConfig):
        _block_all(blocked_setup)
        with patch(
            "model_manager.domain.fallbacks.generate_fallbacks_yaml", return_value=None
        ), patch(
            "model_manager.domain.model_group_aliases.generate_aliases_yaml",
            return_value=None,
        ), patch(
            "model_manager.domain.router_settings.generate_router_settings_yaml",
            return_value=None,
        ):
            result = run_generate_all(blocked_setup, dry_run=False)
        assert result["errors"] == []
        assert result["ok"]["hf"] is True
        assert any("empty" in s and "hf" in s for s in result["steps"])
        out = blocked_setup.providers["hf"].output_path
        assert out is not None and out.exists()
        assert yaml.safe_load(out.read_text()) == {"model_list": []}

    def test_generate_all_empty_dry_run(self, blocked_setup: AppConfig):
        _block_all(blocked_setup)
        with patch(
            "model_manager.domain.fallbacks.generate_fallbacks_yaml", return_value=None
        ), patch(
            "model_manager.domain.model_group_aliases.generate_aliases_yaml",
            return_value=None,
        ), patch(
            "model_manager.domain.router_settings.generate_router_settings_yaml",
            return_value=None,
        ):
            result = run_generate_all(blocked_setup, dry_run=True)
        assert result["errors"] == []
        assert yaml.safe_load(result["outputs"]["hf"]) == {"model_list": []}


def _check_files(tmp_path: Path, *, prov_text: str, stub_alias: str = "my-alias",
                 rs_fallback_subject: str = "my-alias") -> AppConfig:
    (tmp_path / "litellm.yaml").write_text("include:\n  - prov.yaml\n  - rs.yaml\n")
    (tmp_path / "prov.yaml").write_text(prov_text)
    (tmp_path / "fallbacks.yaml").write_text("fallbacks: []\n")
    (tmp_path / "aliases.yaml").write_text(
        "model_group_alias:\n  tier1: nvidia_nim/m1\n")
    (tmp_path / "stub.yaml").write_text(
        "router_settings:\n  model_group_alias:\n"
        f"    {stub_alias}: nvidia_nim/m1\n"
        "  fallbacks: []\n")
    (tmp_path / "rs.yaml").write_text(
        "router_settings:\n  model_group_alias:\n"
        f"    {stub_alias}: nvidia_nim/m1\n"
        "  fallbacks:\n"
        f"    - {rs_fallback_subject}:\n"
        "        - nvidia_nim/m1\n")
    (tmp_path / "models.json").write_text(
        '{"meta": {}, "models": {"m": {"display_name": "M", "family": "x", '
        '"default_variant": "standard", "variants": {"standard": '
        '{"aa_slug": "m", "scores": {"intelligence": 80, "coding": 80}, '
        '"tags": ["tier-1"], "include_in_litellm": true, '
        '"provider_ids": {"nvidia": {"m1": {}}}}}}}}')
    return AppConfig(
        data_dir=tmp_path,
        litellm_config_path=tmp_path / "litellm.yaml",
        litellm_fallbacks_path=tmp_path / "fallbacks.yaml",
        litellm_aliases_path=tmp_path / "aliases.yaml",
        litellm_router_settings_stub_path=tmp_path / "stub.yaml",
        litellm_router_settings_path=tmp_path / "rs.yaml",
        providers={"nvidia": ProviderConfig(keys=["K"], litellm_prefix="nvidia_nim")},
    )


class TestCheckAdmitsBlockedAndAliased:
    def test_empty_model_list_passes(self, tmp_path: Path):
        from model_manager.domain import litellm_check as check_mod

        cfg = _check_files(tmp_path, prov_text="model_list: []\n")
        report = check_mod.check_litellm_configs(cfg)
        assert report["errors"] == [], report["errors"]

    def test_stub_alias_key_as_fallback_subject_passes(self, tmp_path: Path):
        from model_manager.domain import litellm_check as check_mod

        cfg = _check_files(
            tmp_path,
            prov_text="model_list:\n  - model_name: nvidia_nim/m1\n"
                      "    litellm_params: {model: x/m1}\n",
        )
        report = check_mod.check_litellm_configs(cfg)
        assert report["errors"] == [], report["errors"]

    def test_truly_unknown_subject_still_fails(self, tmp_path: Path):
        from model_manager.domain import litellm_check as check_mod

        cfg = _check_files(
            tmp_path,
            prov_text="model_list:\n  - model_name: nvidia_nim/m1\n"
                      "    litellm_params: {model: x/m1}\n",
            rs_fallback_subject="ghost-model",
        )
        report = check_mod.check_litellm_configs(cfg)
        assert any("rs.yaml" in e and "fallbacks DAG valid" in e
                   for e in report["errors"])


def test_regen_provider_errors_do_not_veto_restart(tmp_path: Path):
    """Valid YAML restarts even when re-generate logged provider errors."""
    from model_manager.domain import schedule as schedule_mod

    cfg = AppConfig(data_dir=tmp_path)
    from tests.test_schedule import _pipeline_mocks

    bad = {"model": "m1", "kind": "model", "status": "gone", "code": "410"}
    with _pipeline_mocks(tmp_path, [bad]):
        with patch(
            "model_manager.domain.generate_all.run_generate_all",
            return_value={"steps": ["generate fallbacks: success"],
                          "errors": ["provider 'hf': all targets blocked"]},
        ), patch(
            "model_manager.domain.schedule._validate_configs", return_value=True
        ), patch(
            "model_manager.domain.restart.request_restart",
            return_value={"timestamp": "t"},
        ) as mock_restart:
            res = schedule_mod.execute_schedule_pipeline(cfg)
    # first restart + re-run restart both requested
    assert mock_restart.call_count == 2
    assert any("provider 'hf'" in e for e in res["errors"])
    assert not any("skipping second restart" in e for e in res["errors"])
