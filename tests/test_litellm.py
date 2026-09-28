"""Tests for the litellm command group."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from model_manager.cli import app

runner = CliRunner()


def test_litellm_config_check_help():
    """Verify litellm config check --help works."""
    result = runner.invoke(app, ["litellm", "config", "check", "--help"])
    assert result.exit_code == 0


def _check_cfg(tmp_path: Path, master_text: str = "general:\n  port: 4000\n") -> Path:
    """Minimal isolated config: all litellm paths under tmp_path."""
    (tmp_path / "litellm.yaml").write_text(master_text)
    (tmp_path / "prov.yaml").write_text(
        "model_list:\n  - model_name: nvidia_nim/m1\n    litellm_params: {model: x/m1}\n"
    )
    (tmp_path / "fallbacks.yaml").write_text("fallbacks: []\n")
    (tmp_path / "aliases.yaml").write_text("model_group_alias:\n  tier1: nvidia_nim/m1\n")
    (tmp_path / "stub.yaml").write_text("router_settings: {}\n")
    (tmp_path / "rs.yaml").write_text(
        "router_settings:\n  model_group_alias:\n    tier1: nvidia_nim/m1\n  fallbacks: []\n"
    )
    (tmp_path / "models.json").write_text(
        '{"meta": {}, "models": {"m": {"display_name": "M", "family": "x", '
        '"default_variant": "standard", "variants": {"standard": '
        '{"aa_slug": "m", "scores": {"intelligence": 80, "coding": 80}, '
        '"tags": ["tier-1"], "include_in_litellm": true, '
        '"provider_ids": {"nvidia": {"m1": {}}}}}}}}'
    )
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'data_dir = "{tmp_path}"\n'
        f'litellm_config_path = "{tmp_path / "litellm.yaml"}"\n'
        f'litellm_fallbacks_path = "{tmp_path / "fallbacks.yaml"}"\n'
        f'litellm_aliases_path = "{tmp_path / "aliases.yaml"}"\n'
        f'litellm_router_settings_stub_path = "{tmp_path / "stub.yaml"}"\n'
        f'litellm_router_settings_path = "{tmp_path / "rs.yaml"}"\n'
        '[providers.nvidia]\nkeys = ["K"]\nlitellm_prefix = "nvidia_nim"\n'
    )
    return cfg_file


def test_litellm_config_check_valid_yaml(tmp_path: Path):
    """Verify a consistent config set passes."""
    cfg_file = _check_cfg(tmp_path)
    (tmp_path / "litellm.yaml").write_text("include:\n  - prov.yaml\n")
    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "All LiteLLM configs valid" in result.stdout


def test_litellm_config_check_invalid_yaml(tmp_path: Path):
    """Verify invalid YAML fails."""
    cfg_file = _check_cfg(tmp_path)
    (tmp_path / "litellm.yaml").write_text("key: [unclosed bracket\n")
    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "check(s) failed" in result.stdout


def test_litellm_config_check_unknown_alias_target(tmp_path: Path):
    """Verify alias pointing at an unregistered model fails (domain level)."""
    from model_manager.config import load_config
    from model_manager.domain import litellm_check as check_mod

    cfg_file = _check_cfg(tmp_path)
    (tmp_path / "aliases.yaml").write_text("model_group_alias:\n  tier1: nvidia_nim/ghost\n")
    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "check(s) failed" in result.stdout
    # content assertion bypasses table truncation
    errors = check_mod.check_litellm_configs(load_config(cfg_file))["errors"]
    assert any("ghost" in e for e in errors)


def test_litellm_config_check_fallback_cycle(tmp_path: Path):
    """Verify a fallback cycle fails."""
    cfg_file = _check_cfg(tmp_path)
    (tmp_path / "fallbacks.yaml").write_text(
        "fallbacks:\n"
        "  - nvidia_nim/m1:\n"
        "      - nvidia_nim/m1\n"
    )
    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "check(s) failed" in result.stdout


def test_litellm_generate_all_help():
    """Verify litellm generate all --help works."""
    result = runner.invoke(app, ["litellm", "generate", "all", "--help"])
    assert result.exit_code == 0
    assert "Generate all LiteLLM configuration files" in result.stdout


def test_litellm_generate_all_dry_run(tmp_path: Path):
    """Verify litellm generate all --dry-run prints provider configs, fallbacks, and aliases."""
    import json
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_file = data_dir / "models.json"
    models_data = {
        "meta": {},
        "models": {
            "gemma-4-31b-it": {
                "display_name": "Gemma 4 31B",
                "family": "gemini",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "gemma-4-31b",
                        "include_in_litellm": True,
                        "tags": ["tier-1"],
                        "scores": {"intelligence": 80, "coding": 80},
                        "provider_ids": {
                            "nvidia": {
                                "google/gemma-4-31b-it": {"assessment": "Up", "availability": 1.0}
                            }
                        },
                    }
                },
            }
        },
    }
    models_file.write_text(json.dumps(models_data))

    stub_file = tmp_path / "stub.yaml"
    stub_file.write_text("router_settings: {}\n")

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'data_dir = "{data_dir}"\n'
        f'litellm_fallbacks_path = "{tmp_path / "fallbacks.yaml"}"\n'
        f'litellm_aliases_path = "{tmp_path / "aliases.yaml"}"\n'
        f'litellm_router_settings_stub_path = "{stub_file}"\n'
        f'litellm_router_settings_path = "{tmp_path / "router_settings.yaml"}"\n'
        '[providers.nvidia]\n'
        'keys = ["KEY_1"]\n'
        'litellm_prefix = "nvidia_nim"\n'
    )

    result = runner.invoke(app, ["litellm", "generate", "all", "--config", str(cfg_file), "--dry-run"])
    assert result.exit_code == 0
    assert "=== nvidia ===" in result.stdout
    assert "=== fallbacks ===" in result.stdout or "fallbacks:" in result.stdout
    assert "=== aliases ===" in result.stdout or "model_group_alias:" in result.stdout
    assert "=== router_settings ===" in result.stdout or "router_settings:" in result.stdout


def test_litellm_generate_all_writes_files(tmp_path: Path):
    """Verify litellm generate all creates all config files."""
    import json
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_file = data_dir / "models.json"
    models_data = {
        "meta": {},
        "models": {
            "gemma-4-31b-it": {
                "display_name": "Gemma 4 31B",
                "family": "gemini",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "gemma-4-31b",
                        "include_in_litellm": True,
                        "tags": ["tier-1"],
                        "scores": {"intelligence": 80, "coding": 80},
                        "provider_ids": {
                            "nvidia": {
                                "google/gemma-4-31b-it": {"assessment": "Up", "availability": 1.0}
                            }
                        },
                    }
                },
            }
        },
    }
    models_file.write_text(json.dumps(models_data))

    nvidia_out = tmp_path / "litellm-nvidia.yaml"
    fallbacks_out = tmp_path / "litellm-fallbacks.yaml"
    aliases_out = tmp_path / "litellm-aliases.yaml"
    rs_out = tmp_path / "litellm-router_settings.yaml"
    stub_file = tmp_path / "stub.yaml"
    stub_file.write_text("router_settings: {}\n")

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'data_dir = "{data_dir}"\n'
        f'litellm_fallbacks_path = "{fallbacks_out}"\n'
        f'litellm_aliases_path = "{aliases_out}"\n'
        f'litellm_router_settings_stub_path = "{stub_file}"\n'
        f'litellm_router_settings_path = "{rs_out}"\n'
        '[providers.nvidia]\n'
        'keys = ["KEY_1"]\n'
        'litellm_prefix = "nvidia_nim"\n'
        f'output_path = "{nvidia_out}"\n'
    )

    result = runner.invoke(app, ["litellm", "generate", "all", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert nvidia_out.exists()
    assert fallbacks_out.exists()
    assert aliases_out.exists()
    assert rs_out.exists()


def test_litellm_generate_all_build_cost_map(tmp_path: Path, monkeypatch):
    """Verify litellm generate all --build-cost-map calls cost_map.build_local_cost_map."""
    import json
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_file = data_dir / "models.json"
    models_data = {
        "meta": {},
        "models": {
            "gemma-4-31b-it": {
                "display_name": "Gemma 4 31B",
                "family": "gemini",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "gemma-4-31b",
                        "include_in_litellm": True,
                        "tags": ["tier-1"],
                        "scores": {"intelligence": 80, "coding": 80},
                        "provider_ids": {
                            "nvidia": {
                                "google/gemma-4-31b-it": {"assessment": "Up", "availability": 1.0}
                            }
                        },
                    }
                },
            }
        },
    }
    models_file.write_text(json.dumps(models_data))

    cost_map_built = False

    def mock_build_cost_map(cfg, source_url=None):
        nonlocal cost_map_built
        cost_map_built = True
        return tmp_path / "cost_map.json"

    from model_manager.domain import cost_map
    monkeypatch.setattr(cost_map, "build_local_cost_map", mock_build_cost_map)

    cfg_file = tmp_path / "config.toml"
    stub_file = tmp_path / "stub.yaml"
    stub_file.write_text("router_settings: {}\n")
    cfg_file.write_text(
        f'data_dir = "{data_dir}"\n'
        f'litellm_fallbacks_path = "{tmp_path / "fallbacks.yaml"}"\n'
        f'litellm_aliases_path = "{tmp_path / "aliases.yaml"}"\n'
        f'litellm_router_settings_stub_path = "{stub_file}"\n'
        f'litellm_router_settings_path = "{tmp_path / "router_settings.yaml"}"\n'
        '[providers.nvidia]\n'
        'keys = ["KEY_1"]\n'
        'litellm_prefix = "nvidia_nim"\n'
        f'output_path = "{tmp_path / "nvidia.yaml"}"\n'
    )

    result = runner.invoke(app, ["litellm", "generate", "all", "--config", str(cfg_file), "--build-cost-map"])
    assert result.exit_code == 0
    assert cost_map_built is True
    assert "Successfully built cost map!" in result.stdout


def test_litellm_config_check_missing(tmp_path: Path):
    """Verify missing file fails."""
    cfg_file = tmp_path / "config.toml"
    litellm_cfg = tmp_path / "nonexistent" / "litellm.yaml"
    cfg_file.write_text(f'litellm_config_path = "{litellm_cfg}"\n')

    result = runner.invoke(app, ["litellm", "config", "check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "check(s) failed" in result.stdout