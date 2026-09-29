"""Tests for artifact path resolution and git publishing."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.config import AppConfig, DashboardConfig, get_dashboard_output_path
from model_manager.dashboard import generate_dashboard
from model_manager.domain.git import ArtifactGitError, publish_artifact_git
from model_manager.domain.schedule import execute_schedule_pipeline

runner = CliRunner()


def test_path_resolution_precedence(tmp_path: Path):
    """Verify output path resolution precedence: default < config < override flag."""
    data_dir = tmp_path / "data"
    cfg = AppConfig(data_dir=data_dir)

    # 1. Historical default (no custom config)
    assert get_dashboard_output_path(cfg) == data_dir / "dashboard.html"

    # 2. Config section override
    custom_dir = tmp_path / "custom_dash"
    cfg.dashboard = DashboardConfig(enabled=True, out_dir=str(custom_dir), out_file="report.html")
    assert get_dashboard_output_path(cfg) == custom_dir / "report.html"

    # 3. Disabled config section falls back to historical default
    cfg.dashboard.enabled = False
    assert get_dashboard_output_path(cfg) == data_dir / "dashboard.html"

    # 4. Explicit override wins over everything
    override_path = tmp_path / "explicit" / "dash.html"
    assert get_dashboard_output_path(cfg, override=override_path) == override_path


def test_custom_output_path_generation(tmp_path: Path):
    """Verify generate_dashboard creates parent directories and writes file."""
    cfg = AppConfig(data_dir=tmp_path / "data")
    target = tmp_path / "nested" / "deep" / "status.html"

    output = generate_dashboard(cfg, out_path=target)

    assert output == target
    assert target.exists()
    assert target.read_text().startswith("<!DOCTYPE html>")


@pytest.mark.skipif(not shutil.which("git"), reason="git executable not found")
def test_publish_artifact_git_workflow(tmp_path: Path):
    """Test git publishing against local git repo and bare remote."""
    # Create local bare remote
    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    # Create working repo
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(work_dir)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=work_dir, check=True)

    # Initial commit so HEAD exists
    initial_file = work_dir / "README.md"
    initial_file.write_text("# Repo")
    subprocess.run(["git", "add", "README.md"], cwd=work_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=work_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=work_dir, check=True)

    artifact = work_dir / "dashboard.html"
    artifact.write_text("<h1>Dashboard v1</h1>")

    # 1. First publish
    res1 = publish_artifact_git(artifact, branch="main")
    assert res1["committed"] is True
    assert res1["pushed"] is True
    assert res1["commit"] is not None
    assert res1["branch"] == "main"

    # Verify remote has the commit
    proc = subprocess.run(
        ["git", "log", "-1", "main", "--pretty=%B"],
        cwd=remote_dir, capture_output=True, text=True, check=True
    )
    assert "Update dashboard.html" in proc.stdout

    # 2. Second publish with unchanged content -> no-op
    res2 = publish_artifact_git(artifact, branch="main")
    assert res2["committed"] is False
    assert res2["pushed"] is False
    assert res2["reason"] == "no changes"

    # 3. Third publish with changed content -> commit and push
    artifact.write_text("<h1>Dashboard v2</h1>")
    res3 = publish_artifact_git(artifact, branch="main")
    assert res3["committed"] is True
    assert res3["pushed"] is True


def test_publish_artifact_git_non_repo(tmp_path: Path):
    """Publishing outside a git repo raises ArtifactGitError."""
    artifact = tmp_path / "dash.html"
    artifact.write_text("content")

    with pytest.raises(ArtifactGitError, match="not inside a git repository"):
        publish_artifact_git(artifact)


def test_cli_out_and_git_flags(tmp_path: Path):
    """Test CLI flags --out, --git-push, and --no-git-push."""
    out_file = tmp_path / "cli_dash.html"
    cfg_file = tmp_path / "config.toml"

    # 1. --out writes to given path
    result = runner.invoke(app, ["dashboard", "--out", str(out_file), "--no-open", "-c", str(cfg_file)])
    assert result.exit_code == 0
    assert out_file.exists()

    # 2. --git-push in non-repo directory prints stderr warning and exits 0
    result_git = runner.invoke(app, ["dashboard", "--out", str(out_file), "--git-push", "--no-open", "-c", str(cfg_file)])
    assert result_git.exit_code == 0
    assert "Warning: Git publish failed" in result_git.stderr


def test_schedule_pipeline_git_publish(tmp_path: Path):
    """Test schedule pipeline runs dashboard generation and handles git publish."""
    cfg = AppConfig(
        data_dir=tmp_path,
        dashboard=DashboardConfig(enabled=True, git_enabled=True, git_branch="main"),
    )

    with patch("model_manager.domain.scores.get_api_key", return_value="k"), \
         patch("model_manager.domain.scores.fetch_aa_data", return_value={"m": 1}), \
         patch("model_manager.domain.scores.process_aa_data"), \
         patch("model_manager.domain.scores.merge_agentic_scores"), \
         patch("model_manager.domain.scores.sync_scores_to_models", return_value=1), \
         patch("model_manager.domain.tags.assign_tier_tags", return_value=(1, {}, 60.0)), \
         patch("model_manager.domain.providers.list_providers", return_value=[]), \
         patch("model_manager.domain.generate_all.run_generate_all", return_value={"steps": [], "errors": []}), \
         patch("model_manager.domain.schedule._validate_configs", return_value=True), \
         patch("model_manager.domain.restart.request_restart", return_value={"timestamp": "t"}), \
         patch("model_manager.domain.auth.get_secret", return_value="master_key"), \
         patch("model_manager.domain.schedule._wait_for_proxy", return_value=True), \
         patch("model_manager.domain.litellm_scan.enumerate_targets", return_value={"models": [], "aliases": []}), \
         patch("model_manager.domain.litellm_scan.scan_targets", return_value=[]), \
         patch("model_manager.domain.git.publish_artifact_git", return_value={"committed": True, "pushed": True, "commit": "sha123"}) as mock_pub:

        res = execute_schedule_pipeline(cfg)

    mock_pub.assert_called_once()
    assert res["errors"] == []
    assert any("git committed=True, pushed=True" in s for s in res["steps"])
    assert res["git"]["pushed"] is True
