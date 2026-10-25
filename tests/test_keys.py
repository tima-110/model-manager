"""Tests for API key parsing and Gemini key rotation."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig
from model_manager.domain import keys

runner = CliRunner()


def test_parse_env_file_basic(tmp_path: Path):
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "# Comment line\n"
        "GEMINI_KEY_1=AIzaSyKey1\n"
        'export GEMINI_KEY_2="AIzaSyKey2"\n'
        "GEMINI_KEY_3='AIzaSyKey3'\n"
        "  GEMINI_KEY_4 = AIzaSyKey4  \n"
        "INVALID_LINE\n"
        "\n",
        encoding="utf-8",
    )

    parsed = keys.parse_env_file(env_file)
    assert parsed == {
        "GEMINI_KEY_1": "AIzaSyKey1",
        "GEMINI_KEY_2": "AIzaSyKey2",
        "GEMINI_KEY_3": "AIzaSyKey3",
        "GEMINI_KEY_4": "AIzaSyKey4",
    }


def test_parse_env_file_nonexistent(tmp_path: Path):
    missing_file = tmp_path / "does_not_exist.env"
    assert keys.parse_env_file(missing_file) == {}


def test_get_gemini_keys_from_env_file(tmp_path: Path):
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "GEMINI_KEY_A=VAL_A\n"
        "GEMINI_KEY_B=VAL_B\n"
        "OTHER_KEY=OTHER_VAL\n",
        encoding="utf-8",
    )

    cfg = AppConfig(data_dir=tmp_path, litellm_keys_env_path=env_file)
    result = keys.get_gemini_keys(config=cfg)
    assert set(result) == {"VAL_A", "VAL_B"}


def test_get_gemini_keys_fallback_to_os_env(tmp_path: Path):
    missing_file = tmp_path / "missing.env"
    cfg = AppConfig(data_dir=tmp_path, litellm_keys_env_path=missing_file)

    with patch.dict(os.environ, {"GEMINI_API_KEY": "ENV_VAL_GEMINI"}, clear=True):
        result = keys.get_gemini_keys(config=cfg)
        assert result == ["ENV_VAL_GEMINI"]


def test_get_gemini_keys_fallback_to_keyring(tmp_path: Path):
    missing_file = tmp_path / "missing.env"
    cfg = AppConfig(data_dir=tmp_path, litellm_keys_env_path=missing_file)

    with patch.dict(os.environ, {}, clear=True):
        with patch("model_manager.domain.auth.get_secret", side_effect=lambda k: "KEYRING_GEMINI" if k == "GEMINI_API_KEY" else None):
            result = keys.get_gemini_keys(config=cfg)
            assert result == ["KEYRING_GEMINI"]


def test_get_random_gemini_key_rotation(tmp_path: Path):
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        "GEMINI_KEY_1=KEY_ONE\n"
        "GEMINI_KEY_2=KEY_TWO\n",
        encoding="utf-8",
    )
    cfg = AppConfig(data_dir=tmp_path, litellm_keys_env_path=env_file)

    selected = {keys.get_random_gemini_key(config=cfg) for _ in range(50)}
    assert selected == {"KEY_ONE", "KEY_TWO"}


def test_get_random_gemini_key_raises_when_missing(tmp_path: Path):
    missing_file = tmp_path / "missing.env"
    cfg = AppConfig(data_dir=tmp_path, litellm_keys_env_path=missing_file)

    with patch.dict(os.environ, {}, clear=True):
        with patch("model_manager.domain.auth.get_secret", return_value=None):
            with pytest.raises(RuntimeError, match="No Gemini API keys found"):
                keys.get_random_gemini_key(config=cfg)


def test_cli_gemini_key_commands(tmp_path: Path):
    env_file = tmp_path / "keys.env"
    env_file.write_text("GEMINI_KEY_1=CLI_TEST_KEY\n", encoding="utf-8")

    # 1. model-manager providers gemini key -e ...
    res1 = runner.invoke(app, ["providers", "gemini", "key", "-e", str(env_file)])
    assert res1.exit_code == 0
    assert res1.stdout.strip() == "CLI_TEST_KEY"

    # 2. model-manager providers gemini get-key -e ...
    res2 = runner.invoke(app, ["providers", "gemini", "get-key", "-e", str(env_file)])
    assert res2.exit_code == 0
    assert res2.stdout.strip() == "CLI_TEST_KEY"

    # 3. model-manager providers gemini-key -e ...
    res3 = runner.invoke(app, ["providers", "gemini-key", "-e", str(env_file)])
    assert res3.exit_code == 0
    assert res3.stdout.strip() == "CLI_TEST_KEY"


def test_cli_gemini_key_error(tmp_path: Path):
    missing_file = tmp_path / "missing.env"
    with patch.dict(os.environ, {}, clear=True):
        with patch("model_manager.domain.auth.get_secret", return_value=None):
            res = runner.invoke(app, ["providers", "gemini", "key", "-e", str(missing_file)])
            assert res.exit_code == 1
            assert "Error:" in res.stderr or "Error:" in res.stdout
