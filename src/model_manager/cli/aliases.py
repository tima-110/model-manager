"""CLI commands for managing model identifier mappings."""
from __future__ import annotations

import typer
from pathlib import Path
from rich.table import Table

from model_manager.config import load_config
from model_manager.domain import aliases, models
from .common import console

aliases_app = typer.Typer(help="Manage model identifier mappings.")

@aliases_app.command("resolve")
def aliases_resolve(
    identifier: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Resolve a provider ID or a conceptual model ID to its details and scores."""
    cfg = load_config(config)

    model_res = models.resolve_model(identifier, cfg)
    if model_res:
        table = Table(title=f"Model Summary: {model_res['display_name']}")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("ID", model_res["model"])
        table.add_row("Family", model_res["family"])
        table.add_row("Default Variant", model_res["default_variant"])
        console.print(table)

        for var in model_res["variants"]:
            var_table = Table(title=f"Variant: {var['variant_id']}")
            var_table.add_column("Provider", style="cyan")
            var_table.add_column("IDs", style="green")

            for prov, pids in var["provider_ids"].items():
                var_table.add_row(prov, ", ".join(pids))

            console.print(var_table)
            if var["aa_slug"]:
                console.print(f"  [bold]AA Slug:[/bold] {var['aa_slug']}")
        return

    result = aliases.resolve_id(identifier, cfg)

    if not result:
        console.print(f"[red]Error: No mapping found for {identifier}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Resolution for {identifier}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="magenta")

    table.add_row("Model", result["model"])
    table.add_row("Variant", result["variant"])
    table.add_row("AA Slug", result["aa_slug"])

    console.print(table)

    if result["scores"]:
        s = result["scores"]["scores"]
        score_table = Table(title="Scores")
        score_table.add_column("Metric", style="cyan")
        score_table.add_column("Value", style="green")
        score_table.add_row("Intelligence", str(s.get("intelligence")))
        score_table.add_row("Coding", str(s.get("coding")))
        score_table.add_row("Math", str(s.get("math")))
        console.print(score_table)
    else:
        console.print("[yellow]No scores found for this AA slug.[/yellow]")

@aliases_app.command("add")
def aliases_add(
    model: str,
    variant: str = "standard",
    family: str | None = None,
    display_name: str | None = None,
    aa_slug: str | None = None,
    provider: str | None = None,
    provider_id: str | None = None,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Add or update a model mapping.

    To create a skeleton model, omit the provider and provider_id.
    """
    cfg = load_config(config)
    aliases.add_alias(cfg, model, provider, provider_id, variant, family, display_name, aa_slug)
    if provider and provider_id:
        console.print(f"[green]Mapped {provider_id} to {model} ({variant})[/green]")
    else:
        console.print(f"[green]Created skeleton model {model}[/green]")

@aliases_app.command("discover")
def aliases_discover(
    provider: str,
    ids: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Suggest mappings for unmapped IDs."""
    cfg = load_config(config)
    id_list = ids.split(",")
    suggestions = aliases.discover_aliases(cfg, provider, id_list)

    if not suggestions:
        console.print("[yellow]No suggestions found or all IDs already mapped.[/yellow]")
        return

    for sug in suggestions:
        console.print(f"\n[bold]Unmapped ID:[/bold] {sug['pid']}")
        for i, s in enumerate(sug['suggestions'], 1):
            console.print(f"  {i}. {s}")

        choice = typer.prompt("Accept first suggestion? (y/n)", default="n")
        if choice.lower() == 'y':
            slug = sug['suggestions'][0]
            aliases.add_alias(cfg, provider, sug['pid'], slug, "standard", family="unknown", display_name=slug, aa_slug=slug)
            console.print(f"[green]Mapped {sug['pid']} to {slug}[/green]")

@aliases_app.command("audit")
def aliases_audit(
    ids: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Audit mapping coverage for a list of IDs."""
    cfg = load_config(config)
    id_list = ids.split(",")
    report = aliases.audit_mappings(cfg, id_list)

    table = Table(title="Mapping Audit Report")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta")
    table.add_row("Total", str(report["total"]))
    table.add_row("Mapped", str(report["mapped"]))
    table.add_row("Missing", str(report["missing"]))

    console.print(table)
    if report["missing_ids"]:
        console.print("\n[red]Missing IDs:[/red] ")
        for mid in report["missing_ids"]:
            console.print(f" - {mid}")
