"""Entry point for model-manager."""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from model_manager.cli import app
    except PermissionError:
        print("Error: cannot access current directory.", file=sys.stderr)
        sys.exit(1)
    app()
