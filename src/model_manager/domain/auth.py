"""Secure secret management using the system keychain."""
from __future__ import annotations

import os
import keyring
from typing import Optional

SERVICE_NAME = "model-manager"

def get_secret(key_name: str) -> Optional[str]:
    """
    Retrieve a secret using the priority:
    1. Environment Variable (e.g., OPENROUTER_API_KEY)
    2. OS Keychain
    """
    # 1. Check environment variables first (allows easy overrides for CI/CD)
    env_val = os.environ.get(key_name)
    if env_val:
        return env_val

    # 2. Fallback to OS keychain
    return keyring.get_password(SERVICE_NAME, key_name)

def set_secret(key_name: str, value: str) -> None:
    """Store a secret in the OS keychain."""
    keyring.set_password(SERVICE_NAME, key_name, value)

def delete_secret(key_name: str) -> None:
    """Remove a secret from the OS keychain."""
    try:
        keyring.delete_password(SERVICE_NAME, key_name)
    except keyring.errors.PasswordDeleteError:
        pass
