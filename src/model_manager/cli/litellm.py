"""CLI commands for LiteLLM service management."""
from __future__ import annotations

import typer
from pathlib import Path
from typing import Optional

from model_manager.config import load_config
from model_manager.domain import cost_map
from .common import console

litellm_app = typer.Typer(help="Manage LiteLLM service configuration.")

# Group for cost-map operations
cost_map_app = typer.Typer(help="Build and manage the model cost and context map.")
litellm_app.add_typer(cost_map_app, name="cost-map")

@cost_map_app.command("build")
def cost_map_build(
    config: Path | None = typer.Option(None, "--config", "-c"),
    source_url: Optional[str] = typer.Option(None, "--source-url", help="Override the upstream JSON source URL."),
) -> None:
    """
    Build the LiteLLM cost map by merging upstream data with local overrides.

    The final file is saved to the service directory configured in config.toml.
    """
    cfg = load_config(config)

    try:
        with console.status("[bold green]Building LiteLLM cost map...") as status:
            output_path = cost_map.build_local_cost_map(cfg, source_url=source_url)

        console.print(f"[green]Successfully built cost map![/green]")
        console.print(f"Output path: [cyan]{output_path}[/cyan]")

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}[/red]")
        raise typer.Exit(1)
