"""Tests for LiteLLM YAML generation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from model_manager.config import AppConfig, ProviderConfig
from model_manager.domain import yaml_gen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def models_with_variants(tmp_path: Path) -> Path:
    """Sample models.json with multiple models, variants, and providers."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    models = {
        "meta": {"last_updated": "2026-08-03T00:00:00Z"},
        "models": {
            "gemma-4-31b-it": {
                "display_name": "Gemma 4 31B It",
                "family": "gemini",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "gemma-4-31b",
                        "provider_ids": {
                            "nvidia": ["google/gemma-4-31b-it"],
                            "gemini": ["gemma-4-31b-it"],
                        },
                    },
                },
            },
            "nemotron-3-ultra": {
                "display_name": "Nvidia Nemotron Ultra",
                "family": "nvidia",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "nvidia-nemotron-3-ultra-550b-a55b",
                        "provider_ids": {
                            "nvidia": ["nvidia/nemotron-3-ultra-550b-a55b"],
                        },
                    },
                },
            },
            "deepseek-v4": {
                "display_name": "Deepseek V4",
                "family": "unknown",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "deepseek-v4-pro-high",
                        "provider_ids": {
                            "nvidia": ["deepseek-ai/deepseek-v4-pro"],
                        },
                    },
                    "flash": {
                        "aa_slug": "deepseek-v4-flash-high",
                        "provider_ids": {
                            "nvidia": ["deepseek-ai/deepseek-v4-flash"],
                            "openrouter": ["deepseek/deepseek-v4-flash"],
                        },
                    },
                },
            },
        },
    }
    (data_dir / "models.json").write_text(json.dumps(models))
    return data_dir


@pytest.fixture
def nvidia_scan_all_ok(tmp_path: Path, models_with_variants: Path) -> Path:
    """Scan results where every model is 'up'."""
    scan = {
        "metadata": {"provider": "nvidia"},
        "models": {
            "google/gemma-4-31b-it": {
                "summary": {"assessment": "Up"},
            },
            "nvidia/nemotron-3-ultra-550b-a55b": {
                "summary": {"assessment": "Up"},
            },
            "deepseek-ai/deepseek-v4-pro": {
                "summary": {"assessment": "Up"},
            },
            "deepseek-ai/deepseek-v4-flash": {
                "summary": {"assessment": "Up"},
            },
        },
    }
    (models_with_variants / "nvidia_scan.json").write_text(json.dumps(scan))
    return models_with_variants


@pytest.fixture
def nvidia_scan_mixed(tmp_path: Path, models_with_variants: Path) -> Path:
    """Scan with one model unauthorized, others in various states."""
    scan = {
        "metadata": {"provider": "nvidia"},
        "models": {
            "google/gemma-4-31b-it": {
                "summary": {"assessment": "Up"},
            },
            "nvidia/nemotron-3-ultra-550b-a55b": {
                "summary": {"assessment": "Up"},
            },
            "deepseek-ai/deepseek-v4-pro": {
                "summary": {"assessment": "unauthorized"},
            },
            "deepseek-ai/deepseek-v4-flash": {
                "summary": {"assessment": "down"},
            },
        },
    }
    (models_with_variants / "nvidia_scan.json").write_text(json.dumps(scan))
    return models_with_variants


@pytest.fixture
def nvidia_output_config() -> ProviderConfig:
    return ProviderConfig(
        keys=["NVIDIA_KEY_TIMALOSI", "NVIDIA_KEY_CASTOR", "NVIDIA_KEY_POLLUX"],
        output_path=Path("/tmp/litellm-nvidia.yaml"),
        litellm_prefix="nvidia_nim",
        rpm=40,
    )


@pytest.fixture
def gemini_output_config() -> ProviderConfig:
    return ProviderConfig(
        keys=["GEMINI_KEY_TIMALOSI", "GEMINI_KEY_TIM", "GEMINI_KEY_CASTOR", "GEMINI_KEY_A", "GEMINI_KEY_POLLUX"],
        output_path=Path("/tmp/litellm-gemini.yaml"),
        litellm_prefix="gemini",
        rpm=15,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGenerateProviderYaml:
    def test_basic_generation(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """Happy path: generates YAML with all models replicated for each key."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        assert result is not None

        parsed = yaml.safe_load(result)
        model_list = parsed["model_list"]
        assert len(model_list) == 4 * 3  # 4 provider_ids × 3 keys

    def test_no_scan_file_includes_all(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """When no scan file exists, all models are included."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        model_names = {e["model_name"] for e in parsed["model_list"]}
        assert "nvidia_nim/gemma-4-31b-it" in model_names
        assert "nvidia_nim/nemotron-3-ultra-550b-a55b" in model_names
        assert "nvidia_nim/deepseek-v4-pro" in model_names
        assert "nvidia_nim/deepseek-v4-flash" in model_names

    def test_excludes_unauthorized(self, nvidia_scan_mixed: Path, nvidia_output_config: ProviderConfig):
        """Models with 'unauthorized' status should be excluded."""
        config = AppConfig(
            data_dir=nvidia_scan_mixed,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)

        # deepseek-v4-pro had 1 provider_id that was unauthorized — should be gone
        model_names = {e["model_name"] for e in parsed["model_list"]}
        assert "nvidia_nim/deepseek-v4-pro" not in model_names
        # deepseek-v4-flash was 'down' — should still be included
        assert "nvidia_nim/deepseek-v4-flash" in model_names

    def test_includes_dead_models(self, nvidia_scan_mixed: Path, nvidia_output_config: ProviderConfig):
        """Models with 'down' status should still be included."""
        config = AppConfig(
            data_dir=nvidia_scan_mixed,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        model_names = {e["model_name"] for e in parsed["model_list"]}
        assert "nvidia_nim/deepseek-v4-flash" in model_names

    def test_key_replication(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """Each model should appear once per configured API key."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)

        # Count entries for a single model
        gemma_entries = [e for e in parsed["model_list"] if e["model_name"] == "nvidia_nim/gemma-4-31b-it"]
        assert len(gemma_entries) == 3  # 3 keys

        keys_used = {e["litellm_params"]["api_key"] for e in gemma_entries}
        assert keys_used == {
            "os.environ/NVIDIA_KEY_TIMALOSI",
            "os.environ/NVIDIA_KEY_CASTOR",
            "os.environ/NVIDIA_KEY_POLLUX",
        }

    def test_rpm_in_params(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """RPM should be included in litellm_params."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        entry = parsed["model_list"][0]
        assert entry["litellm_params"]["rpm"] == 40

    def test_model_name_uses_last_segment(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """Model name should derive from the last / segment of provider_id."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        model_names = {e["model_name"] for e in parsed["model_list"]}

        assert "nvidia_nim/gemma-4-31b-it" in model_names
        assert "nvidia_nim/nemotron-3-ultra-550b-a55b" in model_names  # full last segment, no stripping
        assert "nvidia_nim/deepseek-v4-flash" in model_names

    def test_no_provider_config_raises(self, models_with_variants: Path):
        """Missing provider output config should raise RuntimeError."""
        config = AppConfig(data_dir=models_with_variants)  # no providers
        with pytest.raises(RuntimeError, match="No output configuration"):
            yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)

    def test_no_keys_raises(self, models_with_variants: Path):
        """Provider config with empty keys should raise RuntimeError."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={
                "nvidia": ProviderConfig(
                    keys=[],
                    output_path=Path("/tmp/out.yaml"),
                    litellm_prefix="nvidia_nim",
                ),
            },
        )
        with pytest.raises(RuntimeError, match="No API keys"):
            yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)

    def test_no_entries_raises(self, models_with_variants: Path):
        """Provider with no matching provider_ids in models.json should raise."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={
                "bogus": ProviderConfig(
                    keys=["KEY"],
                    output_path=Path("/tmp/out.yaml"),
                    litellm_prefix="bogus",
                ),
            },
        )
        with pytest.raises(RuntimeError, match="No entries generated"):
            yaml_gen.generate_provider_yaml(config, "bogus", dry_run=True)

    def test_gemini_generation(self, models_with_variants: Path, gemini_output_config: ProviderConfig):
        """Gemini provider should generate correctly."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"gemini": gemini_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "gemini", dry_run=True)
        assert result is not None
        parsed = yaml.safe_load(result)
        model_list = parsed["model_list"]
        # gemini has 1 provider_id (gemma-4-31b-it) × 5 keys
        assert len(model_list) == 5
        entry = model_list[0]
        assert entry["model_name"] == "gemini/gemma-4-31b-it"
        assert entry["litellm_params"]["rpm"] == 15
        assert entry["litellm_params"]["model"] == "gemini/gemma-4-31b-it"

    def test_output_writes_file(self, models_with_variants: Path, nvidia_output_config: ProviderConfig, tmp_path: Path):
        """Non-dry-run should write YAML to the output path."""
        output_file = tmp_path / "output.yaml"
        config = AppConfig(
            data_dir=models_with_variants,
            providers={
                "nvidia": ProviderConfig(
                    keys=nvidia_output_config.keys,
                    output_path=output_file,
                    litellm_prefix=nvidia_output_config.litellm_prefix,
                    rpm=nvidia_output_config.rpm,
                ),
            },
        )
        yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=False)
        assert output_file.exists()
        content = output_file.read_text()
        assert "nvidia_nim/gemma-4-31b-it" in content
        assert "os.environ/NVIDIA_KEY_TIMALOSI" in content

    def test_api_base_included(self, models_with_variants: Path):
        """When api_base is configured, it should appear in litellm_params."""
        cfg = ProviderConfig(
            keys=["TEST_KEY"],
            output_path=Path("/tmp/out.yaml"),
            litellm_prefix="ollama",
            api_base="https://ollama.com",
        )
        config = AppConfig(
            data_dir=models_with_variants,
            providers={
                "nvidia": ProviderConfig(
                    keys=["TEST_KEY"],
                    output_path=Path("/tmp/out.yaml"),
                    litellm_prefix="nvidia_nim",
                    rpm=40,
                ),
                # models.json doesn't have ollama provider_ids in test data,
                # but we can test that nvidia entries get api_base if set
            },
        )
        # patch the provider config to include api_base
        config.providers["nvidia"].api_base = "https://custom.api.com"
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        entry = parsed["model_list"][0]
        assert entry["litellm_params"]["api_base"] == "https://custom.api.com"

    def test_generate_all_providers(self, models_with_variants: Path, nvidia_output_config: ProviderConfig):
        """Test that the function iterates all provider_ids and generates valid YAML."""
        config = AppConfig(
            data_dir=models_with_variants,
            providers={"nvidia": nvidia_output_config},
        )
        result = yaml_gen.generate_provider_yaml(config, "nvidia", dry_run=True)
        parsed = yaml.safe_load(result)
        for entry in parsed["model_list"]:
            assert "model_name" in entry
            assert "litellm_params" in entry
            assert "model" in entry["litellm_params"]
            assert "api_key" in entry["litellm_params"]