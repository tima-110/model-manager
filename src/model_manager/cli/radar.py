"""CLI command for generating the interactive benchmark radar page."""
from __future__ import annotations

import sys
import typer
from pathlib import Path

from model_manager.config import load_config
from model_manager.domain import git
from model_manager.radar import generate_radar
from .common import console

radar_app = typer.Typer(help="Generate the interactive benchmark radar page.")


@radar_app.callback(invoke_without_command=True)
def radar(
    no_open: bool = typer.Option(False, "--no-open", help="Generate without opening browser"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to custom config.toml"),
    out: Path | None = typer.Option(None, "--out", help="Write radar page to this exact path."),
    git_push: bool | None = typer.Option(None, "--git-push/--no-git-push", help="Force git push on or off for this run."),
) -> None:
    """Generate a static interactive radar page and open in browser."""
    cfg = load_config(config)

    output = generate_radar(cfg, out_path=out)

    should_push = git_push if git_push is not None else (cfg.radar.enabled and cfg.radar.git_enabled)

    if should_push:
        try:
            git.publish_artifact_git(output, branch=cfg.radar.git_branch)
        except git.ArtifactGitError as e:
            sys.stderr.write(f"Warning: Git publish failed: {e}\n")

    if no_open:
        console.print(f"Radar written to [bold]{output}[/bold]")
    else:
        import webbrowser

        console.print(f"Radar: [bold]{output}[/bold]")
        webbrowser.open(output.as_uri())
