"""CLI implementation for model-manager."""
from __future__ import annotations

import sys
import os
import yaml
import json
import time
import typer
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from enum import Enum

class SortOption(str, Enum):
    alpha = "alpha"
    int = "int"
    code = "code"
    math = "math"
    ttft = "ttft"
    tps = "tps"

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live

from model_manager.config import AppConfig, load_config, save_config, get_free_models_path, get_nvidia_models_path, get_ollama_models_path
from model_manager.domain import aliases, scores, advisor, discovery, auth, models, providers

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

# --- Models Group ---
models_app = typer.Typer(help="Manage the conceptual model library.")
app.add_typer(models_app, name="models")

@models_app.command("list")
def models_list(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """List all defined conceptual model IDs."""
    cfg = load_config(config)
    model_ids = models.list_models(cfg)

    if not model_ids:
        console.print("[yellow]No conceptual models defined in models.json.[/yellow]")
        return

    table = Table(title="Conceptual Models")
    table.add_column("Model ID", style="cyan")
    for mid in model_ids:
        table.add_row(mid)

    console.print(table)

@models_app.command("add")
def models_add(
    model: str,
    family: str | None = typer.Option(None, "--family", "-f"),
    display_name: str | None = typer.Option(None, "--display-name", "-d"),
    default_variant: str | None = typer.Option(None, "--default-variant", "-v"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Add or update a conceptual model.

    Example: model-manager model add my-model --family LLM --display-name "My Model"
    """
    cfg = load_config(config)
    models.add_model(cfg, model, family=family, display_name=display_name, default_variant=default_variant)
    console.print(f"[green]Successfully added/updated conceptual model {model}[/green]")

@models_app.command("remove")
def models_remove(
    model: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Remove a conceptual model from the library."""
    cfg = load_config(config)
    if models.remove_model(cfg, model):
        console.print(f"[green]Successfully removed conceptual model {model}[/green]")
    else:
        console.print(f"[red]Error: Conceptual model {model} not found.[/red]")

@models_app.command("discover")
def models_discover(
    model_id: str,
    provider: str | None = typer.Option(None, "--provider", "-p"),
    refresh: bool = typer.Option(False, "--refresh"),
    yolo: bool = typer.Option(False, "--yolo"),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Discover and map provider IDs to a conceptual model.

    Interactive workflow: Model Identification -> Variant Definition -> Provider Mapping.
    """
    cfg = load_config(config)

    if refresh:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            progress.add_task(description="Refreshing provider caches...", total=None)

            # OpenRouter
            try:
                data = discovery.fetch_openrouter_free_models()
                discovery.save_free_models(cfg, data, get_free_models_path(cfg))
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to refresh OpenRouter: {e}[/yellow]")

            # NVIDIA
            try:
                api_key = auth.get_secret("NVIDIA_API_KEY")
                if api_key:
                    data = discovery.fetch_nvidia_models(api_key)
                    discovery.save_free_models(cfg, data, get_nvidia_models_path(cfg))
                else:
                    console.print("[yellow]Warning: NVIDIA_API_KEY missing, skipping NVIDIA refresh.[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to refresh NVIDIA: {e}[/yellow]")

            # Ollama
            try:
                api_key = auth.get_secret("OLLAMA_API_KEY")
                if api_key:
                    data = discovery.fetch_ollama_models(api_key)
                    discovery.save_free_models(cfg, data, get_ollama_models_path(cfg))
                else:
                    console.print("[yellow]Warning: OLLAMA_API_KEY missing, skipping Ollama refresh.[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to refresh Ollama: {e}[/yellow]")

            progress.add_task(description="Refreshing AA scores...", total=None)
            try:
                aa_api_key = scores.get_api_key()
                if aa_api_key:
                    raw = scores.fetch_aa_data(aa_api_key, cfg)
                    scores.process_aa_data(raw, cfg)
                else:
                    console.print("[yellow]Warning: ARTIFICIAL_ANALYSIS_API_KEY missing, skipping AA refresh.[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to refresh AA scores: {e}[/yellow]")

    # --- Phase 1: Model Identification ---
    model_res = models.resolve_model(model_id, cfg)
    if not model_res:
        # Ensure model exists in library first
        models.add_model(cfg, model_id)
        model_res = models.resolve_model(model_id, cfg)

    default_variant = model_res["default_variant"]

    # Check for AA slug in default variant
    existing_slug = None
    for var in model_res["variants"]:
        if var["variant_id"] == default_variant:
            existing_slug = var["aa_slug"]
            break

    if not existing_slug and not yolo:
        console.print(f"\n[bold]Identifying AA model for {model_id}...[/bold]")
        candidates = models.search_aa_candidates(cfg, model_id)
        if candidates:
            table = Table(title="AA Model Candidates")
            table.add_column("#", style="dim", width=3)
            table.add_column("Slug", style="cyan")
            table.add_column("Name", style="magenta")
            for i, c in enumerate(candidates, 1):
                table.add_row(str(i), c["slug"], c["name"])
            console.print(table)

            choice = typer.prompt("Pick a candidate (1-N) or 'skip'", default="skip")
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(candidates):
                    picked_slug = candidates[idx]["slug"]
                    picked_scores = scores.get_scores_for_slug(cfg, picked_slug)
                    aliases.add_alias(cfg, model_id, variant_id=default_variant, aa_slug=picked_slug, scores=picked_scores)
                    console.print(f"[green]Set default AA slug to {picked_slug}[/green]")
                    existing_slug = picked_slug
        else:
            console.print("[yellow]No AA candidates found. Proceeding with string matching only.[/yellow]")

    # --- Phase 2: Variant Definition ---
    if not yolo:
        add_variants = typer.prompt("Would you like to define additional variants for this model (e.g. 'fast', 'cheap')? (y/n)", default="n")
        if add_variants.lower() == 'y':
            while True:
                var_name = typer.prompt("Variant name (or 'done' to finish)")
                if var_name.lower() == 'done':
                    break

                search_slug = typer.prompt(f"Search for AA slug for variant '{var_name}'? (y/n)", default="y")
                if search_slug.lower() == 'y':
                    candidates = models.search_aa_candidates(cfg, model_id) # Simplified search
                    if candidates:
                        table = Table(title=f"AA Candidates for {var_name}")
                        table.add_column("#", style="dim", width=3)
                        table.add_column("Slug", style="cyan")
                        table.add_column("Name", style="magenta")
                        for i, c in enumerate(candidates, 1):
                            table.add_row(str(i), c["slug"], c["name"])
                        console.print(table)

                        choice = typer.prompt("Pick a candidate (1-N) or 'skip'", default="skip")
                        if choice.isdigit():
                            idx = int(choice) - 1
                            if 0 <= idx < len(candidates):
                                picked_slug = candidates[idx]["slug"]
                                picked_scores = scores.get_scores_for_slug(cfg, picked_slug)
                                aliases.add_alias(cfg, model_id, variant_id=var_name, aa_slug=picked_slug, scores=picked_scores)
                                console.print(f"[green]Set AA slug for {var_name} to {picked_slug}[/green]")
                    else:
                        console.print("[yellow]No AA candidates found.[/yellow]")
                else:
                    aliases.add_alias(cfg, model_id, variant_id=var_name)

    # --- Phase 3: Provider Mapping ---
    # Refresh model resolution after identification/definition phases
    model_res = models.resolve_model(model_id, cfg)
    variant_slugs = []
    for var in model_res["variants"]:
        if var["aa_slug"]:
            variant_slugs.append((var["variant_id"], var["aa_slug"]))

    console.print(f"\n[bold]Searching for provider IDs...[/bold]")
    matches = models.discover_provider_ids(cfg, model_id, variant_slugs, provider)
    if not matches:
        console.print(f"[yellow]No provider matches found for {model_id}.[/yellow]")
        return

    console.print(f"\n[bold]Discovery results for {model_id}:[/bold]")

    aa_matches = [m for m in matches if m["method"] == "aa"]
    string_matches = [m for m in matches if m["method"] == "string"]

    # 1. Strong Matches (from AA)
    if aa_matches:
        table = Table(title="Strong Matches (via Artificial Analysis)")
        table.add_column("Provider", style="cyan")
        table.add_column("Provider ID", style="green")
        table.add_column("Variant", style="yellow")
        table.add_column("AA Name", style="magenta")
        table.add_column("TTFT (s)", style="dim")
        table.add_column("TPS", style="dim")

        for m in aa_matches:
            table.add_row(
                m["provider"],
                m["provider_id"],
                m["variant_id"],
                m["aa_name"],
                f"{m.get('ttft', 'N/A')}" if m.get('ttft') is not None else "N/A",
                f"{m.get('tps', 'N/A')}" if m.get('tps') is not None else "N/A"
            )
        console.print(table)

        if yolo:
            for m in aa_matches:
                aliases.add_alias(cfg, model_id, m["provider"], m["provider_id"], m["variant_id"])
                console.print(f"   [dim]Auto-mapped {m['provider']} to {m['variant_id']}...[/dim]")
        else:
            for m in aa_matches:
                if typer.prompt(f"Accept mapping {m['provider']} to {m['variant_id']}? (y/n)", default="y").lower() == 'y':
                    aliases.add_alias(cfg, model_id, m["provider"], m["provider_id"], m["variant_id"])
                    console.print(f"   [green]Mapped![/green]")
                else:
                    console.print(f"   [red]Skipped.[/red]")

    # 2. Suggested Matches (from cache)
    if string_matches:
        console.print("\n[bold]Suggested Matches (via String Matching):[/bold]")
        # Get current variants for selection
        variants = [v["variant_id"] for v in model_res["variants"]]

        for m in string_matches:
            score_str = f" (score: {m['score']:.2f})"
            console.print(f" - {m['provider']}: {m['provider_id']}{score_str}")

            if yolo:
                # Default to standard or first available variant
                var = variants[0] if variants else "standard"
                aliases.add_alias(cfg, model_id, m["provider"], m["provider_id"], var)
                console.print(f"   [dim]Auto-mapped to {var}...[/dim]")
            else:
                # Prompt for variant assignment
                var_options = "\n".join([f"{i+1}. {v}" for i, v in enumerate(variants)])
                choice = typer.prompt(
                    f"Assign {m['provider_id']} to which variant?\n{var_options}\n(or press Enter to skip)",
                    default=""
                )

                if not choice or choice.lower() == 'skip':
                    console.print(f"   [dim]Skipped.[/dim]")
                    continue

                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(variants):
                        var_name = variants[idx]
                    else:
                        var_name = choice
                else:
                    var_name = choice

                aliases.add_alias(cfg, model_id, m["provider"], m["provider_id"], var_name)
                console.print(f"   [green]Mapped to {var_name}![/green]")
    else:
        console.print("\n[dim]No additional suggested matches found in provider caches.[/dim]")

    console.print(f"\n[green]Discovery process complete.[/green]")

# --- Auth Group ---
auth_app = typer.Typer(help="Manage secure API keys in the system keychain.")
app.add_typer(auth_app, name="auth")

@auth_app.command("set")
def auth_set(
    key_name: str,
    value: str,
) -> None:
    """Store an API key in the system keychain.

    Example: model-manager auth set OPENROUTER_API_KEY=sk-or-xxx
    """
    # Support both 'KEY=VALUE' and separate arguments
    if "=" in key_name:
        k, v = key_name.split("=", 1)
    else:
        k = key_name
        v = value if value else ""
        if not v:
            console.print("[red]Error: No value provided for the key.[/red]")
            raise typer.Exit(1)

    auth.set_secret(k, v)
    console.print(f"[green]Successfully stored {k} in the keychain.[/green]")

@auth_app.command("delete")
def auth_delete(
    key_name: str,
) -> None:
    """Remove an API key from the system keychain."""
    auth.delete_secret(key_name)
    console.print(f"[green]Deleted {key_name} from the keychain.[/green]")

@auth_app.command("list")
def auth_list() -> None:
    """List keys currently stored in the keychain for this app."""
    # Keyring doesn't have a built-in 'list' for all services, so we use a fixed list
    tracked_keys = ["OPENROUTER_API_KEY", "ARTIFICIAL_ANALYSIS_API_KEY", "NVIDIA_API_KEY", "OLLAMA_API_KEY"]

    table = Table(title="Stored Secrets")
    table.add_column("Key", style="cyan")
    table.add_column("Status", style="magenta")

    for k in tracked_keys:
        val = auth.get_secret(k)
        status = "[green]Stored[/green]" if val else "[red]Missing[/red]"
        table.add_row(k, status)

    console.print(table)

# --- Providers Group ---
providers_app = typer.Typer(help="Manage supported model providers.")
app.add_typer(providers_app, name="providers")

# --- Provider Sub-Apps ---
openrouter_app = typer.Typer(help="OpenRouter provider commands.")
nvidia_app = typer.Typer(help="NVIDIA provider commands.")
ollama_app = typer.Typer(help="Ollama provider commands.")

providers_app.add_typer(openrouter_app, name="openrouter")
providers_app.add_typer(nvidia_app, name="nvidia")
providers_app.add_typer(ollama_app, name="ollama")

@openrouter_app.command("fetch")
def openrouter_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Query current free models from OpenRouter and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "openrouter")
    _run_discovery_cli_workflow(provider, probe, config)

@openrouter_app.command("scan")
def openrouter_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
) -> None:
    """Scan the current health and performance of OpenRouter models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "openrouter")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down)


@nvidia_app.command("fetch")
def nvidia_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Query current available models from NVIDIA and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "nvidia")
    _run_discovery_cli_workflow(provider, probe, config)

@nvidia_app.command("scan")
def nvidia_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
) -> None:
    """Scan the current health and performance of NVIDIA models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "nvidia")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down)


@ollama_app.command("fetch")
def ollama_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Query current available models from Ollama Cloud and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "ollama")
    _run_discovery_cli_workflow(provider, probe, config)

@ollama_app.command("scan")
def ollama_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
) -> None:
    """Scan the current health and performance of Ollama models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "ollama")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down)


def _run_discovery_cli_workflow(provider: providers.Provider, probe: bool, config: Path | None) -> None:
    """CLI wrapper for the discovery workflow: adds progress bars and reports results."""
    cfg = load_config(config)

    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            progress.add_task(description=f"Fetching models from {provider.name}...", total=None)

            # Use a simple lambda or wrapper if we want to track probe progress specifically
            # but for now, we call the domain workflow.
            models = providers.run_discovery_workflow(provider, cfg, probe)

        if not models:
            console.print(f"[yellow]No models discovered for {provider.name}.[/yellow]")
            return

        table = Table(title=f"Discovered {provider.name} Models ({len(models)})")
        table.add_column("Model ID", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Context", style="green")
        table.add_column("Architecture", style="yellow")

        for m in models:
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
    provider: providers.Provider,
    config: Path | None,
    filter_str: str | None = None,
    only_up: bool = False,
    only_down: bool = False,
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
    cycle_count = 0
    max_cycles = cfg.scan_count

    def get_status_color(status: str) -> str:
        if status == "up": return "green"
        if status == "ratelimit": return "yellow"
        if status in ("unauthorized", "forbidden"): return "magenta"
        return "red"

    def calculate_assessment(results: List[discovery.PingResult]) -> tuple[str, str]:
        """Returns (assessment_label, color)."""
        if not results: return ("Unknown", "white")

        successes = [r for r in results if r.status == "up"]
        avail = len(successes) / len(results)

        if avail > 0.9:
            avg_lat = sum(r.latency_ms for r in successes) / len(successes)
            if avg_lat < 1000: return ("Good", "green")
            return ("Slow", "yellow")

        # Analyze dominant failure mode
        counts = {}
        for r in results: counts[r.status] = counts.get(r.status, 0) + 1
        dominant = max(counts, key=counts.get)

        if dominant in ("unauthorized", "forbidden"): return ("Unauthorized", "magenta")
        if dominant == "not_found": return ("Not Found", "red")
        if dominant == "ratelimit": return ("Ratelimited", "yellow")
        if dominant in ("down", "timeout"): return ("Dead", "red")
        return ("Weak", "yellow")

    # --- Live Scanning Loop ---
    try:
        with Live(console=console, refresh_per_second=4) as live:
            while True:
                cycle_count += 1

                # 1. Perform parallel scan
                results = discovery.scan_models(provider.probe_id, api_key or "", model_ids)

                # 2. Update history and build table
                table = Table(title=f"Health Scan: {provider.name} (Cycle {cycle_count})")
                table.add_column("Model ID", style="cyan")
                table.add_column("Status", justify="center")
                table.add_column("Latency (ms)", justify="right")
                table.add_column("Avg Latency", justify="right")

                for mid in model_ids:
                    res = results.get(mid)
                    if not res:
                        continue

                    # Apply status filters
                    if only_up and res.status != "up":
                        continue
                    if only_down and res.status == "up":
                        continue

                    if res: history[mid].append(res)


                    # Calculate running average
                    m_hist = history[mid]
                    successes = [r.latency_ms for r in m_hist if r.status == "up"]
                    avg_lat = sum(successes)/len(successes) if successes else 0

                    status_text = res.status if res else "Unknown"
                    color = get_status_color(status_text)
                    lat_text = f"{res.latency_ms:.1f}" if res else "N/A"

                    table.add_row(
                        mid,
                        f"[{color}]{status_text}[/{color}]",
                        lat_text,
                        f"{avg_lat:.1f}" if successes else "N/A"
                    )

                live.update(table)

                if max_cycles > 0 and cycle_count >= max_cycles:
                    break

                time.sleep(cfg.scan_frequency)
    except KeyboardInterrupt:
        console.print("\n[yellow]Scan halted by user.[/yellow]")

    # --- Final Assessment Phase ---
    console.print("\n[bold]Final Health Assessment[/bold]")
    summary_table = Table(show_header=True, header_style="bold magenta")
    summary_table.add_column("Model ID", style="cyan")
    summary_table.add_column("Availability", justify="center")
    summary_table.add_column("Avg Latency", justify="right")
    summary_table.add_column("Assessment", justify="center")

    final_results_data = {"metadata": {"provider": provider.name, "cycles": cycle_count, "timestamp": datetime.now().isoformat()}, "models": {}}

    for mid in model_ids:
        m_hist = history[mid]
        successes = [r for r in m_hist if r.status == "up"]
        avail = len(successes) / len(m_hist) if m_hist else 0
        avg_lat = sum(r.latency_ms for r in successes) / len(successes) if successes else 0

        label, color = calculate_assessment(m_hist)

        summary_table.add_row(
            mid,
            f"{avail:.1%}",
            f"{avg_lat:.1f}ms" if successes else "N/A",
            f"[{color}]{label}[/{color}]"
        )

        final_results_data["models"][mid] = {
            "history": [vars(r) for r in m_hist],
            "summary": {"availability": avail, "avg_latency": avg_lat, "assessment": label}
        }

    console.print(summary_table)

    # Save to JSON
    discovery.save_scan_results(cfg, provider.name, final_results_data)
    console.print(f"\n[dim]Results saved to {cfg.data_dir}/{provider.name.lower()}_scan.json[/dim]")

@providers_app.command("scan")
def providers_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
) -> None:
    """Scan health for all supported providers."""
    for provider in providers.list_providers():
        _run_scan_cli_workflow(provider, config, filter, only_up, only_down)


@providers_app.command("fetch")
def providers_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Fetch updates for all supported providers."""
    for provider in providers.list_providers():
        _run_discovery_cli_workflow(provider, probe, config)

@providers_app.command("list")
def providers_list() -> None:
    """List supported providers and their authorization status."""
    supported = providers.list_providers()

    table = Table(title="Supported Providers")
    table.add_column("Provider", style="cyan")
    table.add_column("Authorized", style="magenta")

    for p in supported:
        is_auth = auth.get_secret(p.secret_key)
        status = "[green]Stored[/green]" if is_auth else "[red]Missing[/red]"
        table.add_row(p.name, status)

    console.print(table)

# --- Scores Group ---
scores_app = typer.Typer(help="Manage Artificial Analysis score ingestion.")
app.add_typer(scores_app, name="scores")

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
    sort: SortOption = typer.Option(SortOption.alpha, "--sort", help="Sort the list by the specified metric."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """List model scores in a table.

    --selected-models: Only show models defined in models.json with an aa_slug.
    --filter: Filter the list by name or slug.
    --refresh: Force a refresh of scores from the API before listing.
    --sort: Sort the list (alpha, int, code, math, ttft, tps).
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

    # Prepare data for table
    rows = []
    if selected_models:
        models_data = models.storage.load_models_data(cfg) # Note: using models.storage instead of storage directly as it's imported as 'models' in domain
        # Wait, 'models' is the module src/model_manager/domain/models.py
        # In cli.py, 'from model_manager.domain import aliases, scores, advisor, discovery, auth, models, providers'
        # So 'models' is the module. 'models.storage' is correct if 'storage' is imported in models.py.
        # Let's check if I should use storage.load_models_data(cfg) directly.
        # Looking at cli.py, storage is NOT imported at the top.
        # I will use models.storage.load_models_data(cfg) or import storage.

        # Wait, src/model_manager/domain/models.py has 'from model_manager.domain import storage'
        # So models.storage is the domain.storage module.

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

    # Filter
    if filter:
        f_lower = filter.lower()
        rows = [r for r in rows if f_lower in r["id1"].lower() or f_lower in r["id2"].lower()]

    # Sorting
    if sort == SortOption.alpha:
        rows.sort(key=lambda r: r["id1"].lower())
    elif sort == SortOption.int:
        rows.sort(key=lambda r: r["scores"].get("intelligence") or -float('inf'), reverse=True)
    elif sort == SortOption.code:
        rows.sort(key=lambda r: r["scores"].get("coding") or -float('inf'), reverse=True)
    elif sort == SortOption.math:
        rows.sort(key=lambda r: r["scores"].get("math") or -float('inf'), reverse=True)
    elif sort == SortOption.ttft:
        rows.sort(key=lambda r: r["scores"].get("ttft") or float('inf'))
    elif sort == SortOption.tps:
        rows.sort(key=lambda r: r["scores"].get("tps") or -float('inf'), reverse=True)

    if not rows:
        console.print("[yellow]No models found matching the criteria.[/yellow]")
        return

    # Render Table
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

# --- Aliases Group ---
aliases_app = typer.Typer(help="Manage model identifier mappings.")
app.add_typer(aliases_app, name="aliases")

@aliases_app.command("resolve")
def aliases_resolve(
    identifier: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Resolve a provider ID or a conceptual model ID to its details and scores."""
    cfg = load_config(config)

    # Try forward resolution first (conceptual model ID)
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

    # Fallback to reverse resolution (provider ID)
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
                console.print(f"[green]Mapped {sug['pid']} to {slug}[/green] ")

    console.print("\n[green]Sync complete.[/green]")


@app.command("init")
def init(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Initialize default config and data directories."""
    cfg = load_config(config)

    # Initialize configuration file
    config_path = save_config(cfg, config)
    console.print(f"[green]Initialized configuration at: {config_path}[/green]")

    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    # Create stub models.json if it doesn't exist
    from model_manager.domain import storage
    from model_manager.config import get_models_path
    path = get_models_path(cfg)
    if not path.exists():
        storage.save_models_data(cfg, {"meta": {}, "models": {}})
        console.print(f"[green]Created stub models.json at: {path}[/green]")
    else:
        console.print(f"[yellow]models.json already exists at: {path}[/yellow]")

    console.print(f"[green]Initialized data directory at: {cfg.data_dir}[/green]")

if __name__ == "__main__":
    app()
