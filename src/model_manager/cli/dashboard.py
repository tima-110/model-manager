"""CLI command for generating the model-manager dashboard."""
from __future__ import annotations

import typer
from pathlib import Path

from model_manager.config import load_config
from .common import console

dashboard_app = typer.Typer(help="Generate a status dashboard.")


@dashboard_app.command()
def dashboard(
    no_open: bool = typer.Option(False, "--no-open", help="Generate without opening browser"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to custom config.toml"),
) -> None:
    """Generate a status dashboard and open in browser."""
    cfg = load_config(config)

    from model_manager.dashboard import generate_dashboard

    output = generate_dashboard(cfg)

    if no_open:
        console.print(f"Dashboard written to [bold]{output}[/bold]")
    else:
        import webbrowser

        console.print(f"Dashboard: [bold]{output}[/bold]")
        webbrowser.open(output.as_uri())