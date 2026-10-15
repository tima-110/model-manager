"""Secrets env file for the scheduled service.

The interactive CLI reads secrets from the OS keychain, which may be
locked when the systemd timer fires without a login session. To make
scheduled runs deterministic, `schedule install` exports the keyring
entries into a 0600 env file that the service loads (via both
`EnvironmentFile=` and `schedule run --env-file`). `get_secret` is
already environment-first, so no auth changes are needed.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from model_manager.domain.auth import SERVICE_NAME

SERVICE_ENV_FILENAME = "model-manager-schedule.env"


def default_service_env_path() -> Path:
    """Return the env file path next to the service definition.

    Lives alongside the unit files on each platform: the systemd user
    dir on Linux, the LaunchAgents dir on macOS.
    """
    if platform.system().lower() == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / SERVICE_ENV_FILENAME
    return Path.home() / ".config" / "systemd" / "user" / SERVICE_ENV_FILENAME


def export_keyring_to_env(path: Path | None = None) -> int:
    """Write keyring entries for this app's service into an env file.

    Only entries stored under ``SERVICE_NAME`` are exported; everything
    else in the keyring is left alone. Returns the number of keys written.
    The file is created with mode 0600.

    Refuses to overwrite an existing non-empty file when nothing could be
    exported (locked or unreachable keyring would otherwise silently
    destroy the last good copy). Raises RuntimeError in that case, and
    also when the export yields zero keys, so callers never mistake an
    empty file for success.
    """
    import secretstorage

    target = path or default_service_env_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    connection = secretstorage.dbus_init()
    lines: list[str] = []
    written = 0
    locked: list[str] = []

    for collection in secretstorage.get_all_collections(connection):
        try:
            label = collection.get_label()
        except Exception:
            label = "?"
        try:
            if collection.is_locked():
                locked.append(label or "?")
                continue
            items = list(collection.get_all_items())
        except Exception:
            continue
        for item in items:
            try:
                attrs = item.get_attributes() or {}
                if attrs.get("service") != SERVICE_NAME:
                    continue
                username = attrs.get("username") or item.get_label()
                if not username:
                    continue
                secret = item.get_secret()
                if secret is None:
                    lines.append(f"# {username}: <unreadable, skipped>")
                    continue
                value = secret.decode("utf-8", errors="replace")
                lines.append(f"{username}={value}")
                written += 1
            except Exception as e:
                try:
                    name = item.get_label() or "?"
                except Exception:
                    name = "?"
                lines.append(f"# {name}: <skipped: {e}>")

    if written == 0:
        detail = ""
        if locked:
            detail = f" Locked collections skipped: {', '.join(sorted(set(locked)))}."
        if target.exists() and target.stat().st_size > 0:
            raise RuntimeError(
                "Exported 0 keys; leaving the existing env file untouched."
                + detail +
                " Unlock the keychain (log in graphically or unlock the Login"
                " keyring) and re-run `model-manager auth update-env`."
            )
        raise RuntimeError(
            "Exported 0 keys; no env file written." + detail +
            " Unlock the keychain and re-run `model-manager auth update-env`."
        )

    target.write_text("\n".join(lines).rstrip() + "\n" if lines else "", encoding="utf-8")
    os.chmod(target, 0o600)
    return written


def load_env_file(path: Path | None = None) -> int:
    """Load KEY=value pairs from an env file into this process.

    Existing environment variables win (explicit env overrides the file).
    Returns the number of keys set. Missing file counts as zero, not error.
    """
    source = path or default_service_env_path()
    if not source.exists():
        return 0

    loaded = 0
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'").strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded
