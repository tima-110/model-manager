"""Tests for dashboard generation, including the fallback chains section."""
from __future__ import annotations

from pathlib import Path

from model_manager.config import AppConfig
from model_manager.dashboard import generate_dashboard


def _empty_cfg(tmp_path: Path, fallbacks_path: Path | None = None) -> AppConfig:
    return AppConfig(data_dir=tmp_path, litellm_fallbacks_path=fallbacks_path or (tmp_path / "fallbacks.yaml"))


def _write_fallbacks(path: Path) -> None:
    path.write_text(
        "fallbacks:\n"
        "  - nvidia_nim/deepseek-v4-flash:\n"
        "      - openrouter/deepseek-v4-flash:free\n"
        "      - ollama/glm-5.1\n"
        "  - ollama/glm-5.1:\n"
        "      - nvidia_nim/glm-5.1\n"
    )


def test_dashboard_renders_fallback_chains(tmp_path: Path):
    cfg = _empty_cfg(tmp_path)
    _write_fallbacks(cfg.litellm_fallbacks_path)
    output = generate_dashboard(cfg)
    html = output.read_text()

    assert "Fallback Chains" in html
    assert "nvidia_nim/deepseek-v4-flash" in html
    assert "openrouter/deepseek-v4-flash:free" in html
    assert "ollama/glm-5.1" in html
    assert "2 chains, 3 entries" in html


def test_dashboard_missing_fallbacks(tmp_path: Path):
    cfg = _empty_cfg(tmp_path)
    output = generate_dashboard(cfg)
    html = output.read_text()

    assert "Fallback Chains" in html
    assert "litellm generate fallbacks" in html


def test_dashboard_invalid_fallbacks_yaml(tmp_path: Path):
    cfg = _empty_cfg(tmp_path)
    cfg.litellm_fallbacks_path.write_text("fallbacks: [unclosed\n")
    output = generate_dashboard(cfg)
    html = output.read_text()

    assert "Fallback Chains" in html
    assert "&#9679;" in html


def test_dashboard_renders_scores_table(tmp_path: Path):
    cfg = _empty_cfg(tmp_path)
    scores_path = cfg.data_dir / "model_scores.json"
    scores_path.write_text('{"models": {"claude-3-5-sonnet": {"scores": {"intelligence": 90, "coding": 95, "agentic": 85}}}}')
    models_path = cfg.data_dir / "models.json"
    models_path.write_text('{"models": {"anthropic/claude-3-5-sonnet": {"variants": {"standard": {"aa_slug": "claude-3-5-sonnet"}}}}}')
    output = generate_dashboard(cfg)
    html = output.read_text()
    assert "Scores" in html
    assert "Intelligence" in html
    assert "Coding" in html
    assert "Agentic" in html
    assert "sortTable" in html
