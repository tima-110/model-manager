"""CLI commands for high-level model selection and comparison."""
from __future__ import annotations

import typer
from pathlib import Path
from rich.table import Table

from model_manager.config import load_config
from model_manager.domain import advisor
from .common import console

advisor_app = typer.Typer(help="High-level model selection and comparison.")

@advisor_app.command("compare")
def advisor_compare(
    ids: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Compare multiple models side-by-side."""
    cfg = load_config(config)
    id_list = ids.split(",")
    results = advisor.compare_models(cfg, id_list)

    table = Table(title="Model Comparison")
    table.add_column("Provider ID", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Variant", style="yellow")
    table.add_column("Intel", style="green")
    table.add_column("Coding", style="green")
    table.add_column("Math", style="green")

    for r in results:
        if "error" in r:
            table.add_row(r["provider_id"], "[red]Error[/red]", "-", "-", "-", "-")
        else:
            s = r["scores"]
            table.add_row(
                r["provider_id"],
                r["model"],
                r["variant"],
                str(s.get("intelligence", "N/A")),
                str(s.get("coding", "N/A")),
                str(s.get("math", "N/A"))
            )

    console.print(table)

@advisor_app.command("best")
def advisor_best(
    metric: str = typer.Option("intelligence", help="Metric to optimize (intelligence, coding, math)"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Find the best mapped model for a specific metric."""
    cfg = load_config(config)
    best = advisor.find_best_model(cfg, metric)

    if not best:
        console.print("[red]No models with scores found.[/red]")
        raise typer.Exit(1)

    console.print(f"  Model:   [green]{best['model']}[/green] ({best['variant']})")
    console.print(f"  Slug:    {best['slug']}")
    console.print(f"  Score:   [bold]{best['score']}[/bold]")

@advisor_app.command("gaps")
def advisor_gaps(
    ids: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Report mapping gaps for a list of IDs."""
    cfg = load_config(config)
    id_list = ids.split(",")
    missing = advisor.get_mapping_gaps(cfg, id_list)

    if not missing:
        console.print("[green]No mapping gaps found![/green]")
    else:
        console.print(f"Found {len(missing)} missing mappings:")
        for mid in missing:
            console.print(f" - {mid}")
