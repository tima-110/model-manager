"""Utilities for reading API keys and rotating Gemini keys."""
from __future__ import annotations

import os
import random
from pathlib import Path

from model_manager.config import AppConfig, load_config
from model_manager.domain import auth

DEFAULT_KEYS_ENV_PATH = Path("/etc/litellm/keys.env")


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs from an env file.

    Handles leading 'export ', ignores empty lines and '#' comments,
    and strips quotes surrounding values.
    """
    if not path.exists() or not path.is_file():
        return {}

    env_map: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching surrounding quotes if present
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1].strip()

        if key and value:
            env_map[key] = value

    return env_map


def get_gemini_keys(
    env_file_path: Path | None = None,
    config: AppConfig | None = None,
) -> list[str]:
    """Locate all available Gemini key values.

    Reads from the given env file path (or config.litellm_keys_env_path,
    defaulting to /etc/litellm/keys.env). If the env file does not exist or
    yields no keys, falls back to inspecting OS environment variables and
    the application secret store (keyring).
    """
    cfg = config or load_config()
    target_path = env_file_path or getattr(cfg, "litellm_keys_env_path", DEFAULT_KEYS_ENV_PATH)

    configured_names = set()
    gemini_cfg = cfg.providers.get("gemini")
    if gemini_cfg and gemini_cfg.keys:
        configured_names.update(gemini_cfg.keys)

    # 1. Check env file if present
    parsed = parse_env_file(target_path)
    file_keys: list[str] = []
    if parsed:
        for k, v in parsed.items():
            k_upper = k.upper()
            if k_upper.startswith("GEMINI") or k in configured_names:
                if v and v not in file_keys:
                    file_keys.append(v)

    if file_keys:
        return file_keys

    # 2. Fallback to process environment variables
    env_keys: list[str] = []
    for k, v in os.environ.items():
        k_upper = k.upper()
        if k_upper.startswith("GEMINI") or k in configured_names:
            if v and v not in env_keys:
                env_keys.append(v)

    if env_keys:
        return env_keys

    # 3. Fallback to keyring / auth secret store
    keyring_keys: list[str] = []
    names_to_check = list(configured_names) if configured_names else ["GEMINI_API_KEY"]
    for name in names_to_check:
        sec = auth.get_secret(name)
        if sec and sec not in keyring_keys:
            keyring_keys.append(sec)

    return keyring_keys


def get_random_gemini_key(
    env_file_path: Path | None = None,
    config: AppConfig | None = None,
) -> str:
    """Randomly select and return one Gemini key value.

    Raises RuntimeError if no Gemini keys can be found.
    """
    keys = get_gemini_keys(env_file_path=env_file_path, config=config)
    if not keys:
        target = env_file_path or (config.litellm_keys_env_path if config else DEFAULT_KEYS_ENV_PATH)
        raise RuntimeError(
            f"No Gemini API keys found in '{target}' or environment."
        )
    return random.choice(keys)
