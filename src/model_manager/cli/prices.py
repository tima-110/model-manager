"""CLI commands for managing the local model price library."""
from __future__ import annotations

import json
import typer
from pathlib import Path

from model_manager.config import get_prices_path, load_config
from model_manager.domain import prices as prices_mod
from .common import console

prices_app = typer.Typer(help="Manage the local model price library (model_prices.json).")


@prices_app.command("fetch")
def prices_fetch(
    config: Path | None = typer.Option(None, "--config", "-c"),
    source_url: str | None = typer.Option(None, "--source-url", help="Override the upstream cost map URL."),
    skip_openrouter: bool = typer.Option(False, "--skip-openrouter", help="Skip the OpenRouter catalog source."),
    skip_upstream: bool = typer.Option(False, "--skip-upstream", help="Skip the upstream LiteLLM cost map source."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build model_prices.json from overrides + OpenRouter + upstream cost map."""
    cfg = load_config(config)
    try:
        path = prices_mod.build_price_library(
            cfg,
            source_url=source_url,
            skip_openrouter=skip_openrouter,
            skip_upstream=skip_upstream,
        )
    except Exception as e:
        console.print(f"[red]Error building price library: {e}[/red]")
        raise typer.Exit(1)
    try:
        total = json.loads(path.read_text()).get("meta", {}).get("total_entries", "?")
    except Exception:
        total = "?"
    if json_output:
        console.print(json.dumps({"path": str(path), "total_entries": total}, indent=2))
    else:
        console.print(f"[green]Price library written to [bold]{path}[/bold] ({total} entries).[/green]")


@prices_app.command("list")
def prices_list(
    filter: str | None = typer.Option(None, "--filter", "-f"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List entries in the local price library."""
    from rich.table import Table

    cfg = load_config(config)
    prices = prices_mod.load_prices(cfg)
    if not prices:
        console.print("[yellow]No price library found. Run 'prices fetch' first.[/yellow]")
        return
    rows = sorted(prices.items())
    if filter:
        f_lower = filter.lower()
        rows = [(k, v) for k, v in rows if f_lower in k]
    if not rows:
        console.print("[yellow]No prices found matching the criteria.[/yellow]")
        return
    if json_output:
        console.print(json.dumps({k: v for k, v in rows}, indent=2))
        return
    table = Table(title=f"Model Prices ({len(rows)})")
    table.add_column("Key", style="cyan")
    table.add_column("Blended $/1M", justify="right", style="green")
    table.add_column("Source", style="magenta")
    for key, entry in rows:
        table.add_row(key, str(entry.get("blended_per_1m", "N/A")), str(entry.get("source", "?")))
    console.print(table)
    console.print(f"[dim]Library: {get_prices_path(cfg)}[/dim]")
