"""Tests for the scheduled service secrets env file."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.domain import service_env
from model_manager.domain.auth import SERVICE_NAME

runner = CliRunner()


def _fake_item(service: str, username: str, secret: bytes | None) -> MagicMock:
    item = MagicMock()
    item.get_attributes.return_value = {"service": service, "username": username}
    item.get_label.return_value = username
    item.get_secret.return_value = secret
    return item


def _fake_collection(items: list[MagicMock]) -> MagicMock:
    collection = MagicMock()
    collection.get_label.return_value = "test"
    collection.get_all_items.return_value = items
    return collection


def test_export_filters_to_app_service(tmp_path: Path):
    target = tmp_path / "svc.env"
    items = [
        _fake_item(SERVICE_NAME, "KEY_ONE", b"secret-1"),
        _fake_item("other-app", "OTHER", b"nope"),
        _fake_item(SERVICE_NAME, "KEY_TWO", b"secret-2"),
    ]
    with patch(
        "secretstorage.dbus_init", return_value=MagicMock()
    ), patch(
        "secretstorage.get_all_collections",
        return_value=[_fake_collection(items)],
    ):
        count = service_env.export_keyring_to_env(target)

    assert count == 2
    text = target.read_text()
    assert "KEY_ONE=secret-1" in text
    assert "KEY_TWO=secret-2" in text
    assert "OTHER" not in text
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_load_env_file_precedence_and_format(tmp_path: Path):
    env_file = tmp_path / "svc.env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "PLAIN=abc\n"
        'QUOTED="with spaces"\n'
        "SINGLE='xyz'\n"
        "BADLINE\n"
    )
    os.environ["PLAIN"] = "explicit-wins"
    try:
        loaded = service_env.load_env_file(env_file)
    finally:
        pass
    assert loaded == 2  # QUOTED + SINGLE; PLAIN kept its explicit value
    assert os.environ["PLAIN"] == "explicit-wins"
    assert os.environ["QUOTED"] == "with spaces"
    assert os.environ["SINGLE"] == "xyz"
    del os.environ["PLAIN"]
    del os.environ["QUOTED"]
    del os.environ["SINGLE"]


def test_load_env_file_missing_is_zero(tmp_path: Path):
    assert service_env.load_env_file(tmp_path / "absent.env") == 0


def test_default_path_in_systemd_user_dir(tmp_path: Path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        path = service_env.default_service_env_path()
    assert path == tmp_path / ".config" / "systemd" / "user" / "model-manager-schedule.env"


def test_default_path_in_launch_agents_on_macos(tmp_path: Path):
    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("platform.system", return_value="Darwin"):
        path = service_env.default_service_env_path()
    assert path == tmp_path / "Library" / "LaunchAgents" / "model-manager-schedule.env"


def test_cli_auth_update_env(tmp_path: Path):
    target = tmp_path / "svc.env"
    with patch(
        "model_manager.domain.service_env.export_keyring_to_env", return_value=4
    ) as mock_export:
        result = runner.invoke(app, ["auth", "update-env", "--output", str(target)])
    assert result.exit_code == 0
    assert "Wrote 4 keys" in result.stdout
    mock_export.assert_called_once_with(target)
