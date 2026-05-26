"""Tests for model aliasing and resolution logic."""
from __future__ import annotations

import pytest
from model_manager.domain import aliases
from model_manager.config import AppConfig

def test_resolve_explicit(mock_config):
    """Verify that a provider ID is resolved via explicit mapping."""
    result = aliases.resolve_id("google/gemma-4-31b", mock_config)
    assert result is not None
    assert result["model"] == "gemma-4-31b"
    assert result["variant"] == "standard"
    assert result["aa_slug"] == "gemma-4-31b"
    assert result["scores"]["scores"]["intelligence"] == 40

def test_resolve_not_found(mock_config):
    """Verify that an unknown ID returns None."""
    result = aliases.resolve_id("unknown/model", mock_config)
    assert result is None

def test_add_alias(mock_config):
    """Verify that adding a new alias updates the data correctly."""
    aliases.add_alias(
        mock_config,
        provider="openrouter",
        provider_id="openrouter/gpt-4",
        model_id="gpt-4",
        variant_id="standard",
        aa_slug="gpt-4-aa"
    )

    # Now resolve the newly added alias
    # Note: we need to ensure the scores file also has a gpt-4-aa entry for full resolution
    # but for this test we just check the resolution metadata
    result = aliases.resolve_id("openrouter/gpt-4", mock_config)
    assert result is not None
    assert result["model"] == "gpt-4"
    assert result["aa_slug"] == "gpt-4-aa"

def test_resolve_default_fallback(mock_config):
    """Verify that providing a model ID directly falls back to the default variant."""
    # The sample data has 'gemma-4-31b' as a model key
    result = aliases.resolve_id("gemma-4-31b", mock_config)
    assert result is not None
    assert result["method"] == "default_fallback"
    assert result["variant"] == "standard"
