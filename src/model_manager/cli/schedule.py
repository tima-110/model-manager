"""CLI commands for managing schedule installation and execution."""
from __future__ import annotations

import typer
from pathlib import Path
from rich.table import Table

from model_manager.config import load_config
from model_manager.domain import schedule
from .common import console

schedule_app = typer.Typer(help="Manage automated model updates schedule.")


@schedule_app.command("install")
def schedule_install(
    frequency: str = typer.Option(
        "daily", "--frequency", "-f",
        help="Schedule frequency (daily, hourly, weekly).",
    ),
    time: str = typer.Option(
        "02:00", "--time", "-t",
        help="Execution time in HH:MM format for daily/weekly schedules.",
    ),
    max_scans: int = typer.Option(
        2, "--max-scans", "-m",
        help="Maximum number of scans to run during provider scan-all.",
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Install the automated schedule service/timer."""
    cfg = load_config(config)

    freq_clean = frequency.lower()
    if freq_clean not in ("daily", "hourly", "weekly"):
        console.print(f"[yellow]Warning: Unusual frequency '{frequency}'. Supported values: daily, hourly, weekly.[/yellow]")

    details = schedule.install_schedule(
        cfg,
        frequency=freq_clean,
        time=time,
        max_scans=max_scans,
        config_path=config,
    )

    console.print("[green]Successfully installed model-manager schedule![/green]")
    console.print(f"Frequency: [cyan]{details['frequency']}[/cyan]")
    console.print(f"Time: [cyan]{details['time']}[/cyan]")
    console.print(f"Max Scans: [cyan]{details['max_scans']}[/cyan]")
    if details.get("warning"):
        console.print(f"[yellow]{details['warning']}[/yellow]")


@schedule_app.command("remove")
def schedule_remove(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Remove and disable the automated schedule service/timer."""
    cfg = load_config(config)
    schedule.remove_schedule(cfg, config_path=config)

    console.print("[green]Successfully removed model-manager schedule.[/green]")


@schedule_app.command("status")
def schedule_status(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Show the current schedule status and configuration."""
    cfg = load_config(config)
    status_info = schedule.get_schedule_status(cfg)

    table = Table(title="Schedule Configuration & Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="magenta")

    table.add_row("Enabled", "Yes" if status_info["enabled"] else "No")
    table.add_row("Frequency", str(status_info["frequency"]))
    table.add_row("Time", str(status_info["time"]))
    table.add_row("Max Scans", str(status_info["max_scans"]))
    table.add_row("Target OS", str(status_info["os"]))
    table.add_row("Service File Installed", "Yes" if status_info["service_installed"] else "No")

    console.print(table)


@schedule_app.command("run")
def schedule_run(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Execute the full model-manager schedule pipeline immediately."""
    cfg = load_config(config)
    console.print("[bold green]Starting scheduled pipeline execution...[/bold green]")

    results = schedule.execute_schedule_pipeline(cfg)

    for step in results["steps"]:
        console.print(f"  [green]✓[/green] {step}")

    if results["errors"]:
        console.print("\n[yellow]Warnings/Errors during schedule execution:[/yellow]")
        for err in results["errors"]:
            console.print(f"  [red]![/red] {err}")
    else:
        console.print("[bold green]Scheduled pipeline completed successfully.[/bold green]")
