"""CLI commands for managing supported model providers."""
from __future__ import annotations

import json
import typer
from pathlib import Path
from typing import List, Optional, Dict
from rich.table import Table

from model_manager.config import load_config
from model_manager.domain import providers, auth
from .common import console, _run_discovery_cli_workflow, _run_scan_cli_workflow

providers_app = typer.Typer(help="Manage supported model providers.")

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
    max_scans: int | None = typer.Option(None, "--max-scans"),
    debug: bool = typer.Option(False, "--debug", help="Log requests and responses to a JSON file and stdout."),
) -> None:
    """Scan the current health and performance of OpenRouter models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "openrouter")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output, max_scans, debug)

@providers_app.command("fetch-all")
def providers_fetch_all(
    probe: bool = typer.Option(False, "--probe", help="Verify model availability by sending a minimal request."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query current models from all supported providers and save their capabilities."""
    all_providers = providers.list_providers()
    errors: list[str] = []

    for provider in all_providers:
        try:
            _run_discovery_cli_workflow(provider, probe, config, json_output)
        except Exception as e:
            errors.append(f"{provider.name}: {e}")

    if errors:
        console.print("\n[red]Errors during fetch-all:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(1)


@providers_app.command("scan-all")
def providers_scan_all(
    config: Path | None = typer.Option(None, "--config", "-c"),
    filter: str | None = typer.Option(None, "--filter", "-f"),
    only_up: bool = typer.Option(False, "--only-up"),
    only_down: bool = typer.Option(False, "--only-down"),
    json_output: bool = typer.Option(False, "--json"),
    max_scans: int | None = typer.Option(None, "--max-scans"),
    debug: bool = typer.Option(False, "--debug", help="Log requests and responses to a JSON file and stdout."),
) -> None:
    """Scan the current health and performance of all supported providers."""
    all_providers = providers.list_providers()
    errors: list[str] = []

    for provider in all_providers:
        try:
            _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output, max_scans, debug)
        except Exception as e:
            errors.append(f"{provider.name}: {e}")

    if errors:
        console.print("\n[red]Errors during scan-all:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(1)


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
    max_scans: int | None = typer.Option(None, "--max-scans"),
    debug: bool = typer.Option(False, "--debug", help="Log requests and responses to a JSON file and stdout."),
) -> None:
    """Scan the current health and performance of NVIDIA models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "nvidia")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output, max_scans, debug)

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
    max_scans: int | None = typer.Option(None, "--max-scans"),
    debug: bool = typer.Option(False, "--debug", help="Log requests and responses to a JSON file and stdout."),
) -> None:
    """Scan the current health and performance of Ollama models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "ollama")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output, max_scans, debug)

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
    max_scans: int | None = typer.Option(None, "--max-scans"),
    debug: bool = typer.Option(False, "--debug", help="Log requests and responses to a JSON file and stdout."),
) -> None:
    """Scan the current health and performance of Gemini models."""
    provider = next(p for p in providers.list_providers() if p.name.lower() == "gemini")
    _run_scan_cli_workflow(provider, config, filter, only_up, only_down, json_output, max_scans, debug)
