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
from typing import List, Optional, Dict
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
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List all defined conceptual models with variants, providers, and scan status."""
    cfg = load_config(config)
    models_data = models.storage.load_models_data(cfg)
    lib_models = models_data.get("models", {})

    if json_output:
        console.print(json.dumps(models_data, indent=2))
        return

    if not lib_models:
        console.print("[yellow]No conceptual models defined in models.json.[/yellow]")
        return

    all_scores = scores.list_all_scores(cfg)

    table = Table(title="Conceptual Model Library")
    table.add_column("Conceptual Model", style="cyan")
    table.add_column("Variant (Scores)", style="magenta")
    table.add_column("Provider", style="cyan")
    table.add_column("Provider Model ID", style="green")
    table.add_column("Status", justify="center")
    table.add_column("Avail", justify="right", style="dim")
    table.add_column("Latency", justify="right", style="dim")

    last_model = None
    last_variant = None

    for mid, m_info in lib_models.items():
        variants = m_info.get("variants", {})
        if not variants:
            model_display = mid if mid != last_model else ""
            table.add_row(model_display, "[dim]no variants[/dim]", "-", "-", "-", "-", "-")
            last_model = mid
            continue

        for vid, v_info in variants.items():
            # Format scores for the variant
            score_str = ""
            slug = v_info.get("aa_slug")
            if slug and slug in all_scores:
                s = all_scores[slug].get("scores", {})
                metrics = [
                    ("I", s.get("intelligence")),
                    ("C", s.get("coding")),
                    ("M", s.get("math")),
                ]
                scores_list = [f"{label}: {val}" for label, val in metrics if val is not None]
                if scores_list:
                    score_str = f" ({', '.join(scores_list)})"

            provider_ids = v_info.get("provider_ids", {})
            if not provider_ids:
                model_display = mid if mid != last_model else ""
                variant_display = f"{vid}{score_str}" if (vid != last_variant or mid != last_model) else ""
                table.add_row(model_display, variant_display, "-", "-", "-", "-", "-")
                last_model = mid
                last_variant = vid
                continue

            for prov, pids in provider_ids.items():
                if not isinstance(pids, dict):
                    model_display = mid if mid != last_model else ""
                    variant_display = f"{vid}{score_str}" if (vid != last_variant or mid != last_model) else ""
                    table.add_row(model_display, variant_display, prov, "[red]Invalid data[/red]", "-", "-", "-")
                    last_model = mid
                    last_variant = vid
                    continue

                for pid, scan_data in pids.items():
                    # Determine if we should display model and variant names (grouping)
                    model_display = mid if mid != last_model else ""
                    variant_display = f"{vid}{score_str}" if (vid != last_variant or mid != last_model) else ""

                    status = scan_data.get("assessment", "Unknown")
                    # Match status colors from scan workflow
                    status_colors = {
                        "Good": "green",
                        "Slow": "yellow",
                        "Weak": "yellow",
                        "Ratelimited": "yellow",
                        "Unauthorized": "magenta",
                        "Not Found": "red",
                        "Dead": "red",
                    }
                    color = status_colors.get(status, "white")
                    colored_status = f"[{color}]{status}[/{color}]"

                    avail = scan_data.get("availability")
                    avail_str = f"{avail:.1%}" if avail is not None else "N/A"
                    lat = scan_data.get("avg_latency")
                    lat_str = f"{lat:.1f}ms" if lat is not None else "N/A"

                    table.add_row(
                        model_display,
                        variant_display,
                        prov,
                        pid,
                        colored_status,
                        avail_str,
                        lat_str
                    )
                    last_model = mid
                    last_variant = vid

    console.print(table)

@models_app.command("add")
def models_add(
    model: str,
    family: str | None = typer.Option(None, "--family", "-f"),
    display_name: str | None = typer.Option(None, "--display-name", "-d"),
    default_variant: str | None = typer.Option(None, "--default-variant", "-v"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Add or update a conceptual model.

    Example: model-manager model add my-model --family LLM --display-name "My Model"
    """
    cfg = load_config(config)
    models.add_model(cfg, model, family=family, display_name=display_name, default_variant=default_variant)
    if json_output:
        console.print(json.dumps({"status": "success", "model": model}, indent=2))
    else:
        console.print(f"[green]Successfully added/updated conceptual model {model}[/green]")

@models_app.command("remove")
def models_remove(
    model: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a conceptual model from the library."""
    cfg = load_config(config)
    if models.remove_model(cfg, model):
        if json_output:
            console.print(json.dumps({"status": "success", "model": model}, indent=2))
        else:
            console.print(f"[green]Successfully removed conceptual model {model}[/green]")
    else:
        if json_output:
            console.print(json.dumps({"status": "error", "message": f"Conceptual model {model} not found."}, indent=2))
            raise typer.Exit(1)
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
gemini_app = typer.Typer(help="Gemini provider commands.")

providers_app.add_typer(openrouter_app, name="openrouter")
providers_app.add_typer(nvidia_app, name="nvidia")
providers_app.add_typer(ollama_app, name="ollama")
providers_app.add_typer(gemini_app, name="gemini")

@openrouter_app.command("fetch")
def openrouter_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query current free models from OpenRouter and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "openrouter")
    _run_discovery_cli_workflow(provider, probe, config, json_output)

@openrouter_app.command("scan")
def openrouter_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan the current health and performance of OpenRouter models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "openrouter")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output)

@nvidia_app.command("fetch")
def nvidia_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query current available models from NVIDIA and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "nvidia")
    _run_discovery_cli_workflow(provider, probe, config, json_output)

@nvidia_app.command("scan")
def nvidia_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan the current health and performance of NVIDIA models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "nvidia")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output)

@ollama_app.command("fetch")
def ollama_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query current available models from Ollama Cloud and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "ollama")
    _run_discovery_cli_workflow(provider, probe, config, json_output)

@ollama_app.command("scan")
def ollama_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan the current health and performance of Ollama models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "ollama")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output)

@gemini_app.command("fetch")
def gemini_fetch(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query current available models from Gemini and save their capabilities."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "gemini")
    _run_discovery_cli_workflow(provider, probe, config, json_output)

@gemini_app.command("scan")
def gemini_scan(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan the current health and performance of Gemini models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "gemini")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output)

def _run_discovery_cli_workflow(provider: providers.Provider, probe: bool, config: Path | None, json_output: bool = False) -> None:
    """CLI wrapper for the discovery workflow: adds progress bars and reports results."""
    cfg = load_config(config)

    try:
        if not json_output:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
                progress.add_task(description=f"Fetching models from {provider.name}...", total=None)
                models = providers.run_discovery_workflow(provider, cfg, probe)
        else:
            models = providers.run_discovery_workflow(provider, cfg, probe)

        if not models:
            console.print(f"[yellow]No models discovered for {provider.name}.[/yellow]")
            return

        if json_output:
            cache_path = provider.path_fn(cfg)
            if cache_path.exists():
                console.print(cache_path.read_text())
            else:
                console.print(json.dumps({"error": "Cache file not found"}, indent=2))
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
    json_output: bool = False,
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

    # --- Scanning Loop ---
    try:
        live = None
        if not json_output:
            live = Live(console=console, refresh_per_second=4)
            live.start()

        while True:
            cycle_count += 1

            # 1. Perform parallel scan
            results = discovery.scan_models(provider.probe_id, api_key or "", model_ids)

            # 2. Update history and build table
            if not json_output:
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
            else:
                # Still update history in silent mode
                for mid in model_ids:
                    res = results.get(mid)
                    if res:
                        # Apply status filters for history as well to match table behavior
                        if only_up and res.status != "up":
                            continue
                        if only_down and res.status == "up":
                            continue
                        history[mid].append(res)

            if max_cycles > 0 and cycle_count >= max_cycles:
                break

            time.sleep(cfg.scan_frequency)

        if live:
            live.stop()
    except KeyboardInterrupt:
        if not json_output:
            console.print("\n[yellow]Scan halted by user.[/yellow]")
        if live:
            live.stop()

    # --- Final Assessment Phase ---
    final_results_data = {"metadata": {"provider": provider.name, "cycles": cycle_count, "timestamp": datetime.now().isoformat()}, "models": {}}

    for mid in model_ids:
        m_hist = history[mid]
        successes = [r for r in m_hist if r.status == "up"]
        avail = len(successes) / len(m_hist) if m_hist else 0
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
        console.print(summary_table)

    # Update mapped models in models.json with new metrics
    models_data = models.storage.load_models_data(cfg)
    updated = False
    for mid, scan_data in final_results_data["models"].items():
        summary = scan_data["summary"]
        for model_id, model_info in models_data.get("models", {}).items():
            for variant_id, variant_info in model_info.get("variants", {}).items():
                for prov, pids in variant_info.get("provider_ids", {}).items():
                    if prov.lower() == provider.name.lower():
                        if isinstance(pids, dict) and mid in pids:
                            pids[mid].update({
                                "availability": summary["availability"],
                                "avg_latency": summary["avg_latency"],
                                "assessment": summary["assessment"]
                            })
                            updated = True
    if updated:
        models.storage.save_models_data(cfg, models_data)
        if not json_output:
            console.print(f"[dim]Updated mapped models in models.json with current health data[/dim]")

    # Save to JSON
    discovery.save_scan_results(cfg, provider.name, final_results_data)
    if not json_output:
        console.print(f"\n[dim]Results saved to {cfg.data_dir}/{provider.name.lower()}_scan.json[/dim]")