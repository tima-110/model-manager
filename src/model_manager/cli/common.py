"""Shared utilities and state for the model-manager CLI."""
from __future__ import annotations

import json
import select
import sys
import termios
import tty
import typer
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live

from model_manager.config import AppConfig, load_config
from model_manager.domain import auth, blocks, discovery, providers

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

def _read_terminal_key() -> str:
    """Read a single keypress or escape sequence from stdin in raw mode."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                return "ESC"
            seq = sys.stdin.read(1)
            if seq == "[":
                code = sys.stdin.read(1)
                if code == "A":
                    return "UP"
                if code == "B":
                    return "DOWN"
                if code in ("5", "6"):
                    tail = sys.stdin.read(1)
                    if tail == "~":
                        return "PGUP" if code == "5" else "PGDN"
            return "ESC"
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x03":
            return "CTRL_C"
        if ch in ("\x7f", "\x08"):
            return "BACKSPACE"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _render_model_picker(
    models: List[Dict[str, Any]],
    filtered: List[Dict[str, Any]],
    cursor: int,
    query: str,
    provider_name: str,
    fetched_at: str,
    page_size: int = 15,
) -> Panel:
    """Render the interactive model picker panel."""
    lines: List[str] = [
        f"[dim]Fetched: {fetched_at} | Filter:[/dim] [bold cyan]{query}[/bold cyan][dim]_[/dim]",
        "",
    ]
    total = len(filtered)
    if total == 0:
        lines.append("[yellow]  No matching models[/yellow]")
    else:
        start = max(0, min(cursor - page_size // 2, total - page_size))
        end = min(total, start + page_size)
        for idx in range(start, end):
            m = filtered[idx]
            mid = str(m.get("id", ""))
            name = str(m.get("name") or "")
            ctx = m.get("context_length")
            extra_parts = []
            if name and name != mid:
                extra_parts.append(name)
            if ctx:
                extra_parts.append(f"{ctx} ctx")
            extra = f" [dim]({', '.join(extra_parts)})[/dim]" if extra_parts else ""
            if idx == cursor:
                lines.append(f"[bold reverse cyan] > {mid} [/bold reverse cyan]{extra}")
            else:
                lines.append(f"   {mid}{extra}")
        lines.append("")
        lines.append(f"[dim]Showing {start + 1}-{end} of {total} (total cached: {len(models)})[/dim]")

    lines.append("[dim]↑/↓ move • PgUp/PgDn page • Type to filter • Enter select • Esc cancel[/dim]")
    return Panel("\n".join(lines), title=f"[bold]{provider_name} Models[/bold]", border_style="cyan")


def _interactive_select_model(
    models: List[Dict[str, Any]],
    provider_name: str,
    fetched_at: str,
    read_key: Callable[[], str] = _read_terminal_key,
    page_size: int = 15,
) -> Optional[str]:
    """Run an interactive arrow-key model picker over cached provider models."""
    query = ""
    cursor = 0

    def _filtered() -> List[Dict[str, Any]]:
        if not query:
            return models
        q = query.lower()
        return [
            m for m in models
            if q in str(m.get("id", "")).lower() or q in str(m.get("name", "")).lower()
        ]

    with Live(_render_model_picker(models, models, cursor, query, provider_name, fetched_at, page_size), console=console, transient=True, auto_refresh=False) as live:
        while True:
            filtered = _filtered()
            if filtered:
                cursor = max(0, min(cursor, len(filtered) - 1))
            else:
                cursor = 0
            live.update(_render_model_picker(models, filtered, cursor, query, provider_name, fetched_at, page_size), refresh=True)

            key = read_key()
            if key in ("ESC", "CTRL_C"):
                return None
            if key == "ENTER":
                if filtered:
                    return str(filtered[cursor]["id"])
                continue
            if key == "UP":
                if filtered:
                    cursor = (cursor - 1) % len(filtered)
            elif key == "DOWN":
                if filtered:
                    cursor = (cursor + 1) % len(filtered)
            elif key == "PGUP":
                if filtered:
                    cursor = max(0, cursor - page_size)
            elif key == "PGDN":
                if filtered:
                    cursor = min(len(filtered) - 1, cursor + page_size)
            elif key == "BACKSPACE":
                if query:
                    query = query[:-1]
                    cursor = 0
            elif len(key) == 1 and key.isprintable():
                query += key
                cursor = 0


def _pick_provider_model(provider: providers.Provider, cfg: AppConfig) -> str:
    """Load cached models for a provider and run the interactive picker."""
    cache_path = provider.path_fn(cfg)
    if not cache_path.exists():
        console.print(
            f"[red]Error: Provider cache not found at {cache_path}. "
            f"Run 'model-manager providers {provider.name.lower()} fetch' first or pass --model.[/red]"
        )
        raise typer.Exit(1)

    with open(cache_path, "r") as f:
        cache_data = json.load(f)

    models = [m for m in cache_data.get("models", []) if isinstance(m, dict) and m.get("id")]
    if not models:
        console.print(
            f"[red]Error: No models found in {cache_path}. "
            f"Run 'model-manager providers {provider.name.lower()} fetch' first or pass --model.[/red]"
        )
        raise typer.Exit(1)

    fetched_at = cache_data.get("fetched_at") or datetime.fromtimestamp(
        cache_path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not sys.stdin.isatty():
        console.print("[red]Error: --model is required in non-interactive mode.[/red]")
        raise typer.Exit(1)

    selected = _interactive_select_model(models, provider.name, str(fetched_at))
    if not selected:
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(1)
    return selected


def _run_chat_cli_workflow(
    provider: providers.Provider,
    model: Optional[str],
    prompt: Optional[str],
    timeout: Optional[float],
    config: Optional[Path],
    json_output: bool = False,
) -> None:
    """CLI workflow for sending a single test chat prompt to a provider model."""
    cfg = load_config(config)
    api_key = auth.get_secret(provider.secret_key)
    if not api_key:
        msg = f"{provider.secret_key} missing from keychain."
        if json_output:
            console.print(json.dumps({"error": msg}, indent=2))
        else:
            console.print(f"[red]Error: {msg}[/red]")
        raise typer.Exit(1)

    if model is None:
        model = _pick_provider_model(provider, cfg)

    if prompt is None:
        try:
            prompt = typer.prompt("Prompt")
        except (typer.Abort, KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Cancelled.[/yellow]")
            raise typer.Exit(1)

    if not prompt.strip():
        msg = "Prompt cannot be empty."
        if json_output:
            console.print(json.dumps({"error": msg}, indent=2))
        else:
            console.print(f"[red]Error: {msg}[/red]")
        raise typer.Exit(1)

    try:
        if not json_output:
            timeout_note = f", timeout: {timeout}s" if timeout is not None else ""
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                progress.add_task(
                    description=f"Waiting for {provider.name} ({model}{timeout_note})... (Press Ctrl+C to cancel)",
                    total=None,
                )
                result = discovery.chat_model(
                    model_id=model,
                    api_key=api_key,
                    prompt=prompt,
                    provider=provider.probe_id,
                    timeout=timeout,
                )
        else:
            result = discovery.chat_model(
                model_id=model,
                api_key=api_key,
                prompt=prompt,
                provider=provider.probe_id,
                timeout=timeout,
            )
    except KeyboardInterrupt:
        if not json_output:
            console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(130)
    except RuntimeError as e:
        if json_output:
            console.print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(
            json.dumps(
                {
                    "provider": provider.name.lower(),
                    "model": model,
                    "latency_ms": round(result.latency_ms, 2),
                    "code": result.code,
                    "response": result.text,
                    "raw": result.raw,
                },
                indent=2,
            )
        )
        return

    console.print(result.text if result.text else "[dim](empty response)[/dim]")
    console.print(f"\n[dim]({provider.name.lower()} / {model} — {result.latency_ms:.0f} ms)[/dim]")


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        console.print(version("model-manager"))
        raise typer.Exit()
