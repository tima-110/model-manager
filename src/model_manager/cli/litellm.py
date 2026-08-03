"""CLI commands for LiteLLM service management."""
from __future__ import annotations

import typer
import yaml
from pathlib import Path
from typing import Optional

from model_manager.config import load_config
from model_manager.domain import cost_map, yaml_gen
from .common import console

litellm_app = typer.Typer(help="Manage LiteLLM service configuration.")

# Group for config operations
config_app = typer.Typer(help="Inspect and validate LiteLLM configuration.")
litellm_app.add_typer(config_app, name="config")

# Group for cost-map operations
cost_map_app = typer.Typer(help="Build and manage the model cost and context map.")
litellm_app.add_typer(cost_map_app, name="cost-map")

@config_app.command("check")
def config_check(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Validate the LiteLLM config file is present and parseable YAML."""
    cfg = load_config(config)
    litellm_config = cfg.litellm_config_path

    console.print(f"LiteLLM config file: [bold]{litellm_config}[/bold]")

    if not litellm_config.exists():
        console.print("[red]FAIL: File not found[/red]")
        raise typer.Exit(1)

    try:
        yaml.safe_load(litellm_config.read_text())
        console.print("[green]PASS: Valid YAML[/green]")
    except yaml.YAMLError as e:
        console.print(f"[red]FAIL: Invalid YAML — {e}[/red]")
        raise typer.Exit(1)
    except PermissionError:
        console.print("[red]FAIL: Permission denied[/red]")
        raise typer.Exit(1)

@cost_map_app.command("build")
def cost_map_build(
    config: Path | None = typer.Option(None, "--config", "-c"),
    source_url: Optional[str] = typer.Option(None, "--source-url", help="Override the upstream JSON source URL."),
) -> None:
    """
    Build the LiteLLM cost map by merging upstream data with local overrides.

    The final file is saved to the service directory configured in config.toml.
    """
    cfg = load_config(config)

    try:
        with console.status("[bold green]Building LiteLLM cost map...") as status:
            output_path = cost_map.build_local_cost_map(cfg, source_url=source_url)

        console.print(f"[green]Successfully built cost map![/green]")
        console.print(f"Output path: [cyan]{output_path}[/cyan]")

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}[/red]")
        raise typer.Exit(1)


# --- generate-config ---

generate_app = typer.Typer(help="Generate LiteLLM YAML config from models.json.")
litellm_app.add_typer(generate_app, name="generate")

@generate_app.command("config")
def generate_config(
    provider: str = typer.Argument(
        ..., help="Provider name (nvidia, gemini, ollama, openrouter)."
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Override output path for the generated YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the generated YAML to stdout instead of writing to file."),
) -> None:
    """Generate a LiteLLM YAML config file for a specific provider.

    Reads models.json and scan results, derives LiteLLM model names from
    the provider_id mappings, and generates one entry per configured API key.

    Models with scan status "unauthorized" are excluded; all others are included.
    """
    cfg = load_config(config)

    try:
        result = yaml_gen.generate_provider_yaml(
            cfg, provider.lower(),
            dry_run=dry_run,
            output_path=output,
        )
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if dry_run and result:
        console.print(result)
    elif not dry_run:
        out_path = output or (cfg.providers.get(provider.lower()) and cfg.providers[provider.lower()].output_path)
        path_str = str(out_path) if out_path else "configured path"
        console.print(f"[green]Generated config for [bold]{provider}[/bold] at: {path_str}[/green]")
