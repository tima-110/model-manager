"""CLI commands for managing Artificial Analysis score ingestion."""
from __future__ import annotations

import json
import typer
from pathlib import Path
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from model_manager.config import load_config
from model_manager.domain import scores, models
from .common import console

scores_app = typer.Typer(help="Manage Artificial Analysis score ingestion.")

@scores_app.command("fetch")
def scores_fetch(
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


@scores_app.command("sync")
def scores_sync(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Update model variants in models.json with current scores from the local cache."""
    cfg = load_config(config)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
        progress.add_task(description="Syncing scores to models library...", total=None)
        try:
            updated_count = scores.sync_scores_to_models(cfg)
        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    console.print(f"[green]Successfully updated scores for {updated_count} model variants.[/green]")


@scores_app.command("list")
def scores_list(
    filter: str | None = typer.Option(None, "--filter", "-f"),
    refresh: bool = typer.Option(False, "--refresh"),
    selected_models: bool = typer.Option(False, "--selected-models"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List model scores in a table.

    --selected-models: Only show models defined in models.json with an aa_slug.
    --filter: Filter the list by name or slug.
    --refresh: Force a refresh of scores from the API before listing.
    """
    cfg = load_config(config)

    if refresh:
        api_key = scores.get_api_key()
        if not api_key:
            console.print("[red]Error: ARTIFICIAL_ANALYSIS_API_KEY missing. Cannot refresh.[/red]")
            raise typer.Exit(1)

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            progress.add_task(description="Refreshing scores from AA...", total=None)
            raw = scores.fetch_aa_data(api_key, cfg)
            if raw:
                scores.process_aa_data(raw, cfg)
            else:
                console.print("[yellow]Warning: Failed to fetch latest scores. Using cached data.[/yellow]")

    all_scores = scores.list_all_scores(cfg)
    if not all_scores:
        console.print("[yellow]No processed scores found. Run 'scores fetch' first.[/yellow]")
        return

    if json_output and not filter and not selected_models:
        # Simplified JSON output for the base case
        console.print(json.dumps({"models": all_scores}, indent=2))
        return

    rows = []
    if selected_models:
        models_data = models.storage.load_models_data(cfg)
        lib_models = models_data.get("models", {})
        for mid, m_info in lib_models.items():
            for vid, v_info in m_info.get("variants", {}).items():
                slug = v_info.get("aa_slug")
                if slug and slug in all_scores:
                    rows.append({
                        "id1": mid,
                        "id2": vid,
                        "scores": all_scores[slug].get("scores", {})
                    })
    else:
        for slug, s_data in all_scores.items():
            rows.append({
                "id1": s_data.get("name", slug),
                "id2": slug,
                "scores": s_data.get("scores", {})
            })

    if filter:
        f_lower = filter.lower()
        rows = [r for r in rows if f_lower in r["id1"].lower() or f_lower in r["id2"].lower()]

    if not rows:
        console.print("[yellow]No models found matching the criteria.[/yellow]")
        return

    if json_output:
        json_models = {}
        for r in rows:
            json_models[r["id2"]] = {"name": r["id1"], "scores": r["scores"]}
        console.print(json.dumps({"models": json_models}, indent=2))
        return

    title = "Selected Model Scores" if selected_models else "All Model Scores"
    table = Table(title=title)
    table.add_column("Model/Name", style="cyan")
    table.add_column("Variant/Slug", style="magenta")
    table.add_column("Intel", justify="right", style="green")
    table.add_column("Coding", justify="right", style="green")
    table.add_column("Math", justify="right", style="green")
    table.add_column("TTFT (s)", justify="right", style="dim")
    table.add_column("TPS", justify="right", style="dim")

    for r in rows:
        s = r["scores"]
        table.add_row(
            r["id1"],
            r["id2"],
            str(s.get("intelligence", "N/A")),
            str(s.get("coding", "N/A")),
            str(s.get("math", "N/A")),
            str(s.get("ttft", "N/A")),
            str(s.get("tps", "N/A"))
        )

    console.print(table)
