"""Shared utilities and state for the model-manager CLI."""
from __future__ import annotations

import json
import typer
from pathlib import Path
from enum import Enum
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live

from model_manager.config import load_config
from model_manager.domain import auth, blocks, providers

console = Console()

class SortOption(str, Enum):
    alpha = "alpha"
    int = "int"
    code = "code"
    agentic = "agentic"
    ttft = "ttft"
    tps = "tps"

def _run_discovery_cli_workflow(provider, probe: bool, config: Path | None, json_output: bool = False) -> None:
    """CLI wrapper for the discovery workflow: adds progress bars and reports results."""
    cfg = load_config(config)

    try:
        if not json_output:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
                progress.add_task(description=f"Fetching models from {provider.name}...", total=None)
                models_list = providers.run_discovery_workflow(provider, cfg, probe)
        else:
            models_list = providers.run_discovery_workflow(provider, cfg, probe)

        if not models_list:
            console.print(f"[yellow]No models discovered for {provider.name}.[/yellow]")
            return

        try:
            blocks.record_fetch_observations(cfg, provider.name, {m["id"] for m in models_list if m.get("id")})
        except Exception:
            pass

        if json_output:
            cache_path = provider.path_fn(cfg)
            if cache_path.exists():
                console.print(cache_path.read_text())
            else:
                console.print(json.dumps({"error": "Cache file not found"}, indent=2))
            return

        table = Table(title=f"Discovered {provider.name} Models ({len(models_list)})")
        table.add_column("Model ID", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Context", style="green")
        table.add_column("Architecture", style="yellow")

        for m in models_list:
            table.add_row(
                str(m["id"] or "Unknown"),
                str(m["name"] or "Unknown"),
                str(m["context_length"] or "N/A"),
                str(m["architecture"] or "Unknown")
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error during {provider.name} discovery: {e}[/red]")

def _assessment_color(label: str) -> str:
    """Map a scan assessment label to a display color."""
    return {
        "Good": "green",
        "Slow": "yellow",
        "Unauthorized": "magenta",
        "Not Found": "red",
        "Ratelimited": "yellow",
        "Dead": "red",
        "Unsupported": "cyan",
        "Unknown": "white",
    }.get(label, "yellow")


def _run_scan_cli_workflow(
    provider,
    config: Path | None,
    filter_str: str | None = None,
    only_up: bool = False,
    only_down: bool = False,
    json_output: bool = False,
    max_scans: int | None = None,
    debug: bool = False,
) -> None:
    """CLI workflow for scanning provider model health with live updates and final assessment."""
    cfg = load_config(config)
    api_key = auth.get_secret(provider.secret_key)

    if not api_key and provider.name != "OpenRouter":
        console.print(f"[red]Error: {provider.secret_key} missing from keychain.[/red]")
        raise typer.Exit(1)

    # Get models to scan from the provider's cache
    cache_path = provider.path_fn(cfg)
    if not cache_path.exists():
        console.print(f"[red]Error: Provider cache not found at {cache_path}. Please run 'fetch' first.[/red]")
        raise typer.Exit(1)

    live = None
    if not json_output:
        live = Live(console=console, refresh_per_second=4)
        live.start()

    def get_status_color(status: str) -> str:
        if status == "up": return "green"
        if status == "ratelimit": return "yellow"
        if status in ("unauthorized", "forbidden"): return "magenta"
        if status == "unsupported": return "cyan"
        return "red"

    def on_cycle(cycle_count, results, history):
        if json_output:
            return
        table = Table(title=f"Health Scan: {provider.name} (Cycle {cycle_count})")
        table.add_column("Model ID", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Latency (ms)", justify="right")
        table.add_column("Avg Latency", justify="right")

        for mid, res in results.items():
            if not res: continue
            if only_up and res.status != "up": continue
            if only_down and res.status == "up": continue

            m_hist = history.get(mid, [])
            successes = [r.latency_ms for r in m_hist if r.status == "up"]
            avg_lat = sum(successes)/len(successes) if successes else 0
            status_text = res.status if res else "Unknown"
            color = get_status_color(status_text)
            lat_text = f"{res.latency_ms:.1f}" if res else "N/A"

            table.add_row(mid, f"[{color}]{status_text}[/{color}]", lat_text, f"{avg_lat:.1f}" if successes else "N/A")
        live.update(table)

    try:
        summary = providers.scan_provider_models(
            provider, cfg,
            max_scans=max_scans,
            filter_str=filter_str,
            only_up=only_up,
            only_down=only_down,
            debug=debug,
            on_cycle=on_cycle,
        )
    except KeyboardInterrupt:
        if not json_output: console.print("\n[yellow]Scan halted by user.[/yellow]")
        if live: live.stop()
        return
    if live: live.stop()

    if summary["scanned"] == 0:
        console.print(f"[yellow]No models found to scan for {provider.name}.[/yellow]")
        return

    try:
        blocks.record_assessment_observations(cfg, provider.name, summary["assessments"])
    except Exception:
        pass

    final_results_data = summary["results"]

    if debug:
        debug_file = summary["debug_file"]
        if debug_file:
            console.print("\n[bold cyan]Debug Scan Logs[/bold cyan]")
            console.print(f"[dim]Debug logs saved to {debug_file}[/dim]")

    if json_output:
        console.print(json.dumps(final_results_data, indent=2))
    else:
        console.print("\n[bold]Final Health Assessment[/bold]")
        summary_table = Table(show_header=True, header_style="bold magenta")
        summary_table.add_column("Model ID", style="cyan")
        summary_table.add_column("Availability", justify="center")
        summary_table.add_column("Avg Latency", justify="right")
        summary_table.add_column("Assessment", justify="center")

        for mid, scan_data in final_results_data["models"].items():
            s = scan_data["summary"]
            successes = s["availability"] > 0
            color = _assessment_color(s["assessment"])
            summary_table.add_row(mid, f"{s['availability']:.1%}", f"{s['avg_latency']:.1f}ms" if successes else "N/A", f"[{color}]{s['assessment']}[/{color}]")
        console.print(summary_table)

    if summary["models_updated"]:
        if not json_output: console.print(f"[dim]Updated mapped models in models.json with current health data[/dim]")

    if not json_output: console.print(f"\n[dim]Results saved to {cfg.data_dir}/{provider.name.lower()}_scan.json[/dim]")

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        console.print(version("model-manager"))
        raise typer.Exit()
