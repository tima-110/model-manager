"""CLI command for generating the model-manager dashboard."""
from __future__ import annotations

import sys
import typer
from pathlib import Path

from model_manager.config import load_config
from .common import console

dashboard_app = typer.Typer(help="Generate a status dashboard.")


@dashboard_app.callback(invoke_without_command=True)
def dashboard(
    no_open: bool = typer.Option(False, "--no-open", help="Generate without opening browser"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to custom config.toml"),
    out: Path | None = typer.Option(None, "--out", help="Write dashboard to this exact path."),
    git_push: bool | None = typer.Option(None, "--git-push/--no-git-push", help="Force git push on or off for this run."),
) -> None:
    """Generate a status dashboard and open in browser."""
    cfg = load_config(config)

    from model_manager.dashboard import generate_dashboard
    from model_manager.domain.git import publish_artifact_git, ArtifactGitError

    output = generate_dashboard(cfg, out_path=out)

    should_push = git_push if git_push is not None else (cfg.dashboard.enabled and cfg.dashboard.git_enabled)

    if should_push:
        try:
            publish_artifact_git(output, branch=cfg.dashboard.git_branch)
        except ArtifactGitError as e:
            sys.stderr.write(f"Warning: Git publish failed: {e}\n")

    if no_open:
        console.print(f"Dashboard written to [bold]{output}[/bold]")
    else:
        import webbrowser

        console.print(f"Dashboard: [bold]{output}[/bold]")
        webbrowser.open(output.as_uri())