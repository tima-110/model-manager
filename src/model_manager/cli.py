"""CLI implementation for model-manager."""
from __future__ import annotations

import sys
import yaml
import typer
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from model_manager.config import AppConfig, load_config
from model_manager.domain import aliases, scores, advisor

app = typer.Typer(
    name="model-manager",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        console.print(version("model-manager"))
        raise typer.Exit()

@app.callback(invoke_without_command=True)
def root(
    version: bool = typer.Option(
        False, "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c",
        help="Path to custom config.toml",
    ),
    verbose: bool = typer.Option(
        False, "--verbose",
        help="Enable verbose output.",
    ),
) -> None:
    """Manage model rankings and aliases for LiteLLM installations."""

# --- Scores Group ---
scores_app = typer.Typer(help="Manage Artificial Analysis score ingestion.")
app.add_typer(scores_app, name="scores")

@scores_app.command("sync")
def scores_sync(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Fetch latest model scores from Artificial Analysis."""
    cfg = load_config(config)
    api_key = scores.get_api_key()

    if not api_key:
        console.print("[red]Error: ARTIFICIAL_ANALYSIS_API_KEY not found in environment.[/red]")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
        progress.add_task(description="Fetching data from AA...", total=None)
        data = scores.fetch_aa_data(api_key, cfg)

        if not data:
            console.print("[red]Error: Failed to fetch data from AA API.[/red]")
            raise typer.Exit(1)

        progress.add_task(description="Processing and saving scores...", total=None)
        processed = scores.process_aa_data(data, cfg)

    console.print(f"[green]Successfully synced {processed['meta']['total_models']} models.[/green]")

# --- Aliases Group ---
aliases_app = typer.Typer(help="Manage model identifier mappings.")
app.add_typer(aliases_app, name="aliases")

@aliases_app.command("resolve")
def aliases_resolve(
    provider_id: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Resolve a provider ID to its scores."""
    cfg = load_config(config)
    result = aliases.resolve_id(provider_id, cfg)

    if not result:
        console.print(f"[red]Error: No mapping found for {provider_id}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Resolution for {provider_id}")
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
    provider: str,
    id: str,
    model: str,
    variant: str = "standard",
    family: str | None = None,
    display_name: str | None = None,
    aa_slug: str | None = None,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Add or update a model mapping."""
    cfg = load_config(config)
    aliases.add_alias(cfg, provider, id, model, variant, family, display_name, aa_slug)
    console.print(f"[green]Mapped {id} to {model} ({variant})[/green]")

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
        console.print("\n[red]Missing IDs:[/red]")
        for mid in report["missing_ids"]:
            console.print(f" - {mid}")

# --- Advisor Group ---
advisor_app = typer.Typer(help="High-level model selection and comparison.")
app.add_typer(advisor_app, name="advisor")

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

    console.print(f"The best model for [bold]{metric}[/bold] is:")
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

# --- LiteLLM Integration ---
@app.command("sync-litellm")
def sync_litellm(
    config_path: Path = typer.Option("/etc/litellm/litellm.yaml", "--config", "-c"),
    provider: str = typer.Option("openrouter", help="Default provider for discovery"),
) -> None:
    """Sync mappings based on LiteLLM config."""
    app_cfg = load_config(None)

    if not config_path.exists():
        console.print(f"[red]Error: LiteLLM config not found at {config_path}[/red]")
        raise typer.Exit(1)

    try:
        with open(config_path, "r") as f:
            yaml_data = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error parsing YAML: {e}[/red]")
        raise typer.Exit(1)

    model_ids = []
    model_list = yaml_data.get("model_list", [])
    for m in model_list:
        if "model_name" in m:
            model_ids.append(m["model_name"])

    if not model_ids:
        console.print("[yellow]No models found in LiteLLM config.[/yellow]")
        return

    console.print(f"Found {len(model_ids)} models in LiteLLM config. Starting audit...")

    report = aliases.audit_mappings(app_cfg, model_ids)
    console.print(f"Mapped: {report['mapped']} / {report['total']}")

    if report["missing"]:
        console.print(f"Discovering mappings for {len(report['missing'])} missing models...")
        suggestions = aliases.discover_aliases(app_cfg, provider, report["missing"])

        if not suggestions:
            console.print("[yellow]No suggestions found for missing models.[/yellow]")
            return

        for sug in suggestions:
            console.print(f"\n[bold]Unmapped ID:[/bold] {sug['pid']}")
            for i, s in enumerate(sug['suggestions'], 1):
                console.print(f"  {i}. {s}")

            choice = typer.prompt("Accept first suggestion? (y/n)", default="n")
            if choice.lower() == 'y':
                slug = sug['suggestions'][0]
                aliases.add_alias(app_cfg, provider, sug['pid'], slug, "standard", family="unknown", display_name=slug, aa_slug=slug)
                console.print(f"[green]Mapped {sug['pid']} to {slug}[/green]")

    console.print("\n[green]Sync complete.[/green]")

@app.command("init")
def init(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Initialize default config and data directories."""
    cfg = load_config(config)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]Initialized data directory at: {cfg.data_dir}[/green]")

if __name__ == "__main__":
    app()
