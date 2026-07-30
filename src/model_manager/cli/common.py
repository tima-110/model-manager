"""Shared utilities and state for the model-manager CLI."""
from __future__ import annotations

import json
import time
import typer
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from enum import Enum
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live

from model_manager.config import load_config
from model_manager.domain import discovery, auth, models, providers

console = Console()

class SortOption(str, Enum):
    alpha = "alpha"
    int = "int"
    code = "code"
    math = "math"
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

    with open(cache_path, "r") as f:
        cache_data = json.load(f)
        all_models = cache_data.get("models", [])

        if filter_str:
            model_ids = [
                m["id"] for m in all_models
                if filter_str.lower() in m["id"].lower() or filter_str.lower() in m.get("name", "").lower()
            ]
        else:
            model_ids = [m["id"] for m in all_models]

    if not model_ids:
        console.print(f"[yellow]No models found to scan for {provider.name}.[/yellow]")
        return

    # State tracking
    history: Dict[str, List[discovery.PingResult]] = {mid: [] for mid in model_ids}
    debug_logs = []
    cycle_count = 0
    max_cycles = max_scans if max_scans is not None else cfg.scan_count

    def get_status_color(status: str) -> str:
        if status == "up": return "green"
        if status == "ratelimit": return "yellow"
        if status in ("unauthorized", "forbidden"): return "magenta"
        if status == "unsupported": return "cyan"
        return "red"

    def calculate_assessment(results: List[discovery.PingResult]) -> tuple[str, str]:
        """Returns (assessment_label, color).

        Excludes 'unsupported' results from availability calculation
        since those models can't be probed via this scan method.
        """
        if not results: return ("Unknown", "white")

        relevant = [r for r in results if r.status != "unsupported"]
        if not relevant:
            return ("Unsupported", "cyan")

        successes = [r for r in relevant if r.status == "up"]
        avail = len(successes) / len(relevant)

        if avail > 0.9:
            avg_lat = sum(r.latency_ms for r in successes) / len(successes)
            if avg_lat < 1000: return ("Good", "green")
            return ("Slow", "yellow")

        counts = {}
        for r in relevant: counts[r.status] = counts.get(r.status, 0) + 1
        dominant = max(counts, key=counts.get)

        if dominant in ("unauthorized", "forbidden"): return ("Unauthorized", "magenta")
        if dominant == "not_found": return ("Not Found", "red")
        if dominant == "ratelimit": return ("Ratelimited", "yellow")
        if dominant in ("down", "timeout"): return ("Dead", "red")
        return ("Weak", "yellow")

    # Provider-specific scan tuning
    prov_cfg = cfg.providers.get(provider.name.lower(), {})
    scan_concurrency = prov_cfg.scan_concurrency if prov_cfg.scan_concurrency is not None else provider.scan_concurrency
    scan_delay = prov_cfg.scan_delay_between_models_ms if prov_cfg.scan_delay_between_models_ms is not None else provider.scan_delay_between_models_ms
    cycle_delay = prov_cfg.cycle_delay_sec if prov_cfg.cycle_delay_sec is not None else provider.cycle_delay_sec if provider.cycle_delay_sec is not None else cfg.scan_frequency

    try:
        live = None
        if not json_output:
            live = Live(console=console, refresh_per_second=4)
            live.start()

        while True:
            cycle_count += 1
            results = discovery.scan_models(provider.probe_id, api_key or "", model_ids, concurrency=scan_concurrency, delay_between_models_ms=scan_delay, debug=debug)

            if debug:
                for mid, res in results.items():
                    if res and res.debug_info:
                        debug_logs.append({
                            "cycle": cycle_count,
                            "model_id": mid,
                            "debug": res.debug_info
                        })

            if not json_output:
                table = Table(title=f"Health Scan: {provider.name} (Cycle {cycle_count})")
                table.add_column("Model ID", style="cyan")
                table.add_column("Status", justify="center")
                table.add_column("Latency (ms)", justify="right")
                table.add_column("Avg Latency", justify="right")

                for mid in model_ids:
                    res = results.get(mid)
                    if not res: continue
                    if only_up and res.status != "up": continue
                    if only_down and res.status == "up": continue
                    if res: history[mid].append(res)

                    m_hist = history[mid]
                    successes = [r.latency_ms for r in m_hist if r.status == "up"]
                    avg_lat = sum(successes)/len(successes) if successes else 0
                    status_text = res.status if res else "Unknown"
                    color = get_status_color(status_text)
                    lat_text = f"{res.latency_ms:.1f}" if res else "N/A"

                    table.add_row(mid, f"[{color}]{status_text}[/{color}]", lat_text, f"{avg_lat:.1f}" if successes else "N/A")
                live.update(table)
            else:
                for mid in model_ids:
                    res = results.get(mid)
                    if res:
                        if only_up and res.status != "up": continue
                        if only_down and res.status == "up": continue
                        history[mid].append(res)

            if max_cycles > 0 and cycle_count >= max_cycles:
                break
            time.sleep(cycle_delay)
        if live: live.stop()
    except KeyboardInterrupt:
        if not json_output: console.print("\n[yellow]Scan halted by user.[/yellow]")
        if live: live.stop()

    if debug and debug_logs:
        debug_data = {
            "metadata": {
                "provider": provider.name,
                "cycles": cycle_count,
                "timestamp": datetime.utcnow().isoformat()
            },
            "logs": debug_logs
        }
        debug_json = json.dumps(debug_data, indent=2)

        # Output to stdout
        console.print("\n[bold cyan]Debug Scan Logs[/bold cyan]")
        console.print(debug_json)

        # Output to file
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        debug_file = cfg.data_dir / f"debug_scan_{provider.name.lower()}_{ts}.json"
        debug_file.write_text(debug_json)
        console.print(f"[dim]Debug logs saved to {debug_file}[/dim]")

    final_results_data = {"metadata": {"provider": provider.name, "cycles": cycle_count, "timestamp": datetime.utcnow().isoformat()}, "models": {}}

    for mid in model_ids:
        m_hist = history[mid]
        relevant = [r for r in m_hist if r.status != "unsupported"]
        successes = [r for r in relevant if r.status == "up"]
        avail = len(successes) / len(relevant) if relevant else 0
        avg_lat = sum(r.latency_ms for r in successes) / len(successes) if successes else 0
        label, color = calculate_assessment(m_hist)
        final_results_data["models"][mid] = {
            "history": [vars(r) for r in m_hist],
            "summary": {"availability": avail, "avg_latency": avg_lat, "assessment": label}
        }

    if json_output:
        console.print(json.dumps(final_results_data, indent=2))
    else:
        console.print("\n[bold]Final Health Assessment[/bold]")
        summary_table = Table(show_header=True, header_style="bold magenta")
        summary_table.add_column("Model ID", style="cyan")
        summary_table.add_column("Availability", justify="center")
        summary_table.add_column("Avg Latency", justify="right")
        summary_table.add_column("Assessment", justify="center")

        for mid in model_ids:
            m_hist = history[mid]
            relevant = [r for r in m_hist if r.status != "unsupported"]
            successes = [r for r in relevant if r.status == "up"]
            avail = len(successes) / len(relevant) if relevant else 0
            avg_lat = sum(r.latency_ms for r in successes) / len(successes) if successes else 0
            label, color = calculate_assessment(m_hist)
            summary_table.add_row(mid, f"{avail:.1%}", f"{avg_lat:.1f}ms" if successes else "N/A", f"[{color}]{label}[/{color}]")
        console.print(summary_table)

    models_data = models.storage.load_models_data(cfg)
    updated = False
    for mid, scan_data in final_results_data["models"].items():
        summary = scan_data["summary"]
        for model_id, model_info in models_data.get("models", {}).items():
            for variant_id, variant_info in model_info.get("variants", {}).items():
                for prov, pids in variant_info.get("provider_ids", {}).items():
                    if prov.lower() == provider.name.lower():
                        if isinstance(pids, dict) and mid in pids:
                            pids[mid].update({"availability": summary["availability"], "avg_latency": summary["avg_latency"], "assessment": summary["assessment"], "scan_timestamp": final_results_data["metadata"]["timestamp"]})
                            updated = True
    if updated:
        models.storage.save_models_data(cfg, models_data)
        if not json_output: console.print(f"[dim]Updated mapped models in models.json with current health data[/dim]")

    discovery.save_scan_results(cfg, provider.name, final_results_data)
    if not json_output: console.print(f"\n[dim]Results saved to {cfg.data_dir}/{provider.name.lower()}_scan.json[/dim]")

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        console.print(version("model-manager"))
        raise typer.Exit()
