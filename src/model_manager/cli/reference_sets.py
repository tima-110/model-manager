"""CLI commands for managing reference sets."""
from __future__ import annotations

import json
import typer
from pathlib import Path
from rich.table import Table

from model_manager.config import load_config
from model_manager.domain import reference_sets, scores
from .common import console

reference_sets_app = typer.Typer(help="Manage model reference sets.")

@reference_sets_app.command("create")
def reference_sets_create(
    name: str,
    display_name: str | None = typer.Option(None, "--display-name", "-d", help="Display name for the reference set."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Create a new reference set.

    Example: model-manager reference-sets create anthropic --display-name "Anthropic Models"
    """
    cfg = load_config(config)
    reference_sets.create_reference_set(cfg, name, display_name=display_name)
    if json_output:
        console.print(json.dumps({"status": "success", "set_id": name}, indent=2))
    else:
        console.print(f"[green]Successfully created reference set: {name}[/green]")

@reference_sets_app.command("remove")
def reference_sets_remove(
    name: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a reference set.

    Example: model-manager reference-sets remove anthropic
    """
    cfg = load_config(config)
    if reference_sets.remove_reference_set(cfg, name):
        if json_output:
            console.print(json.dumps({"status": "success", "set_id": name}, indent=2))
        else:
            console.print(f"[green]Successfully removed reference set: {name}[/green]")
    else:
        if json_output:
            console.print(json.dumps({"status": "error", "message": f"Reference set {name} not found."}, indent=2))
            raise typer.Exit(1)
        else:
            console.print(f"[red]Error: Reference set {name} not found.[/red]")
            raise typer.Exit(1)

@reference_sets_app.command("add-item")
def reference_sets_add_item(
    name: str,
    model: str,
    variant: str,
    aa_slug: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Add a model variant to a reference set.

    Example: model-manager reference-sets add-item anthropic claude-3 standard claude-3-opus
    """
    cfg = load_config(config)
    if reference_sets.add_item(cfg, name, model, variant, aa_slug):
        if json_output:
            console.print(json.dumps({"status": "success", "set_id": name, "model": model, "variant": variant}, indent=2))
        else:
            console.print(f"[green]Successfully added item {model}/{variant} to reference set: {name}[/green]")
    else:
        if json_output:
            console.print(json.dumps({"status": "error", "message": f"Reference set {name} not found."}, indent=2))
            raise typer.Exit(1)
        else:
            console.print(f"[red]Error: Reference set {name} not found.[/red]")
            raise typer.Exit(1)

@reference_sets_app.command("remove-item")
def reference_sets_remove_item(
    name: str,
    model: str,
    variant: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a model variant from a reference set.

    Example: model-manager reference-sets remove-item anthropic claude-3 standard
    """
    cfg = load_config(config)
    if reference_sets.remove_item(cfg, name, model, variant):
        if json_output:
            console.print(json.dumps({"status": "success", "set_id": name, "model": model, "variant": variant}, indent=2))
        else:
            console.print(f"[green]Successfully removed item {model}/{variant} from reference set: {name}[/green]")
    else:
        if json_output:
            console.print(json.dumps({"status": "error", "message": f"Reference set {name} or item not found."}, indent=2))
            raise typer.Exit(1)
        else:
            console.print(f"[red]Error: Reference set {name} or item not found.[/red]")
            raise typer.Exit(1)

@reference_sets_app.command("list")
def reference_sets_list(
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List all reference sets and their items."""
    cfg = load_config(config)
    ref_sets = reference_sets.list_reference_sets(cfg)

    if json_output:
        console.print(json.dumps(ref_sets, indent=2))
        return

    if not ref_sets:
        console.print("[yellow]No reference sets defined.[/yellow]")
        return

    all_scores = scores.list_all_scores(cfg)

    for set_id, set_data in ref_sets.items():
        display_name = set_data.get("display_name", set_id)
        table = Table(title=f"Reference Set: {display_name} ({set_id})")
        table.add_column("Model", style="cyan")
        table.add_column("Variant", style="magenta")
        table.add_column("AA Slug", style="green")
        table.add_column("Scores (I, C, A)", style="yellow")

        items = set_data.get("items", [])
        if not items:
            table.add_row("[dim]Empty[/dim]", "", "", "")
        else:
            for item in items:
                model = item.get("model", "")
                variant = item.get("variant", "")
                slug = item.get("aa_slug", "")

                score_str = "N/A"
                if slug and slug in all_scores:
                    s = all_scores[slug].get("scores", {})
                    metrics = [
                        ("I", s.get("intelligence")),
                        ("C", s.get("coding")),
                        ("A", s.get("agentic")),
                    ]
                    scores_list = [f"{label}: {val}" for label, val in metrics if val is not None]
                    if scores_list:
                        score_str = ", ".join(scores_list)

                table.add_row(model, variant, slug, score_str)

        console.print(table)
        console.print()
