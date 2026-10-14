"""Tests for variant-level rate/capacity limits (models.json)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_manager.config import AppConfig
from model_manager.domain import models


@pytest.fixture
def limits_data_dir(tmp_path: Path) -> Path:
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
                        "provider_ids": {
                            "gemini": {"gemini/flash": {}},
                            "nvidia": ["nvidia/flash"],  # legacy list shape
                        },
                    },
                },
            },
        },
    }))
    return data_dir


def _load(data_dir: Path) -> dict:
    return json.loads((data_dir / "models.json").read_text())


class TestValidateLimits:
    def test_ok(self):
        assert models.validate_limits({"rpm": 5, "tpm": 100, "weight": 1.5}) == {
            "rpm": 5, "tpm": 100, "weight": 1.5}

    def test_unknown_field(self):
        with pytest.raises(ValueError, match="Unknown limit field"):
            models.validate_limits({"bogus": 1})

    @pytest.mark.parametrize("field", ["rpm", "tpm", "rpd", "max_parallel_requests"])
    @pytest.mark.parametrize("bad", [0, -1, 1.5, "5", True])
    def test_bad_int_values(self, field, bad):
        with pytest.raises(ValueError, match="positive integer"):
            models.validate_limits({field: bad})

    @pytest.mark.parametrize("bad", [0, -1, "2", True])
    def test_bad_weight_values(self, bad):
        with pytest.raises(ValueError, match="positive number"):
            models.validate_limits({"weight": bad})


class TestUpdateVariantLimits:
    def test_set_variant_wide(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(cfg, "m", "standard", values={"rpm": 10}) is True
        variant = _load(limits_data_dir)["models"]["m"]["variants"]["standard"]
        assert variant["limits"] == {"rpm": 10}

    def test_set_provider_scoped(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(
            cfg, "m", "standard", values={"rpm": 5}, provider="gemini") is True
        variant = _load(limits_data_dir)["models"]["m"]["variants"]["standard"]
        assert variant["provider_ids"]["gemini"]["limits"] == {"rpm": 5}
        assert "limits" not in variant

    def test_set_migrates_legacy_list(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(
            cfg, "m", "standard", values={"rpm": 40}, provider="nvidia") is True
        entry = _load(limits_data_dir)["models"]["m"]["variants"]["standard"]["provider_ids"]["nvidia"]
        assert entry["nvidia/flash"] == {}
        assert entry["limits"] == {"rpm": 40}

    def test_unknown_model_or_variant(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(cfg, "nope", "standard", values={"rpm": 1}) is False
        assert models.update_variant_limits(cfg, "m", "nope", values={"rpm": 1}) is False

    def test_unknown_provider(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(
            cfg, "m", "standard", values={"rpm": 1}, provider="bogus") is False

    def test_no_values_raises(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        with pytest.raises(ValueError, match="No limit values"):
            models.update_variant_limits(cfg, "m", "standard", values={})

    def test_invalid_values_raise(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        with pytest.raises(ValueError, match="positive integer"):
            models.update_variant_limits(cfg, "m", "standard", values={"rpm": -1})

    def test_clear_variant_wide(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        models.update_variant_limits(cfg, "m", "standard", values={"rpm": 10})
        assert models.update_variant_limits(cfg, "m", "standard", clear=True) is True
        assert "limits" not in _load(limits_data_dir)["models"]["m"]["variants"]["standard"]

    def test_clear_provider_scoped(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        models.update_variant_limits(cfg, "m", "standard", values={"rpm": 5}, provider="gemini")
        assert models.update_variant_limits(cfg, "m", "standard", provider="gemini", clear=True) is True
        entry = _load(limits_data_dir)["models"]["m"]["variants"]["standard"]["provider_ids"]["gemini"]
        assert "limits" not in entry

    def test_clear_unknown_provider(self, limits_data_dir: Path):
        cfg = AppConfig(data_dir=limits_data_dir)
        assert models.update_variant_limits(cfg, "m", "standard", provider="bogus", clear=True) is False
