"""CLI smoke tests for model-manager."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner
from model_manager.cli import app

runner = CliRunner()

def test_version():
    """Verify --version prints the version and exits."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    # Check if result.stdout contains a version string (e.g., 0.1.0)
    assert len(result.stdout.strip()) > 0

def test_help():
    """Verify --help displays the help message."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout

def test_init():
    """Verify the init command creates data directories."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Initialized data directory" in result.stdout

def test_scores_sync_no_key():
    """Verify scores sync fails gracefully without an API key."""
    # Ensure key is not in environment
    import os
    if "ARTIFICIAL_ANALYSIS_API_KEY" in os.environ:
        del os.environ["ARTIFICIAL_ANALYSIS_API_KEY"]

    result = runner.invoke(app, ["scores", "sync"])
    assert result.exit_code == 1
    assert "Error: ARTIFICIAL_ANALYSIS_API_KEY not found" in result.stdout

def test_aliases_resolve_not_found():
    """Verify resolve command handles unknown IDs."""
    result = runner.invoke(app, ["aliases", "resolve", "unknown-model"])
    assert result.exit_code == 1
    assert "Error: No mapping found" in result.stdout
