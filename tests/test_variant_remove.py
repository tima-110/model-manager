"""Tests for removing a variant from a conceptual model (models.json)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_manager.config import AppConfig
from model_manager.domain import models


def _write_models(data_dir: Path, models_data: dict) -> None:
    (data_dir / "models.json").write_text(json.dumps({"meta": {}, "models": models_data}))


def _load(data_dir: Path) -> dict:
    return json.loads((data_dir / "models.json").read_text())


@pytest.fixture
def variants_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_models(data_dir, {
        "m": {
            "display_name": "M", "family": "x", "default_variant": "standard",
            "variants": {
                "standard": {"aa_slug": "m", "provider_ids": {}},
                "non-reasoning": {"aa_slug": "m-non-reasoning", "provider_ids": {}},
            },
        },
        "only": {
            "display_name": "Only", "family": "x", "default_variant": "standard",
            "variants": {"standard": {"aa_slug": "only", "provider_ids": {}}},
        },
    })
    return data_dir


class TestRemoveVariant:
    def test_removes_variant(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        assert models.remove_variant(cfg, "m", "non-reasoning") is True

        stored = _load(variants_data_dir)["models"]["m"]["variants"]
        assert "non-reasoning" not in stored
        assert "standard" in stored

    def test_keeps_default_when_other_variant_removed(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        models.remove_variant(cfg, "m", "non-reasoning")

        assert _load(variants_data_dir)["models"]["m"]["default_variant"] == "standard"

    def test_reassigns_default_when_default_removed(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        models.remove_variant(cfg, "m", "standard")

        assert _load(variants_data_dir)["models"]["m"]["default_variant"] == "non-reasoning"

    def test_unknown_model_returns_false(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        assert models.remove_variant(cfg, "nope", "standard") is False

    def test_unknown_variant_returns_false(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        assert models.remove_variant(cfg, "m", "nope") is False

    def test_last_variant_raises(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        with pytest.raises(ValueError, match="last variant"):
            models.remove_variant(cfg, "only", "standard")

    def test_bumps_last_updated(self, variants_data_dir: Path):
        cfg = AppConfig(data_dir=variants_data_dir)

        models.remove_variant(cfg, "m", "non-reasoning")

        assert _load(variants_data_dir)["meta"]["last_updated"]
