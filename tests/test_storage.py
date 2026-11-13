"""Tests for atomic storage and corrupt-file protection."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig, get_models_path
from model_manager.domain import models
from model_manager.domain.storage import StorageError, load_models_data, save_models_data

runner = CliRunner()


def test_truncated_models_json_fails_loudly_and_quarantines(tmp_path: Path):
    """Mutating a truncated models.json must fail, quarantine it, and not overwrite."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_path = data_dir / "models.json"
    truncated = '{"models": {"keep-me": {"variants": {}}}'
    models_path.write_text(truncated, encoding="utf-8")

    cfg = AppConfig(data_dir=data_dir)

    with pytest.raises(StorageError):
        models.add_model(cfg, "new-one")

    assert not models_path.exists()
    quarantined = list(data_dir.glob("models.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == truncated
    assert "keep-me" in quarantined[0].read_text(encoding="utf-8")


def test_cli_mutating_command_on_corrupt_models_json_exits_nonzero(tmp_path: Path):
    """CLI `models add` on corrupt models.json fails loudly and preserves the bad file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_path = data_dir / "models.json"
    truncated = '{"models": {"keep-me": {"variants": {}}}'
    models_path.write_text(truncated, encoding="utf-8")

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{data_dir}"\n', encoding="utf-8")

    result = runner.invoke(app, ["models", "add", "new-one", "-c", str(cfg_file)])
    assert result.exit_code != 0
    assert isinstance(result.exception, StorageError)
    assert not models_path.exists()

    quarantined = list(data_dir.glob("models.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == truncated


def test_save_models_data_atomic_failure_preserves_existing_file(tmp_path: Path):
    """If atomic replace fails, original models.json is preserved and temp file is removed."""
    cfg = AppConfig(data_dir=tmp_path)
    original = {"meta": {}, "models": {"keep-me": {"variants": {}}}}
    save_models_data(cfg, original)

    models_path = get_models_path(cfg)
    before = models_path.read_text(encoding="utf-8")

    with patch("model_manager.domain.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            save_models_data(cfg, {"meta": {}, "models": {"wiped": {}}})

    assert models_path.read_text(encoding="utf-8") == before
    assert load_models_data(cfg) == original
    assert list(tmp_path.glob(".*.tmp")) == []
