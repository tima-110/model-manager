"""Main entry point for the model-manager CLI."""
from __future__ import annotations

import typer
from pathlib import Path

from .common import console, _version_callback
from .models import models_app
from .scores import scores_app
from .aliases import aliases_app
from .advisor import advisor_app
from .providers import providers_app
from .auth import auth_app
from .litellm import litellm_app
from .dashboard import dashboard_app
from .doctor import doctor_app
from .schedule import schedule_app

app = typer.Typer(
    name="model-manager",
    no_args_is_help=True,
    add_completion=False,
)

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

# Register command groups
app.add_typer(models_app, name="models")
app.add_typer(scores_app, name="scores")
app.add_typer(aliases_app, name="aliases")
app.add_typer(advisor_app, name="advisor")
app.add_typer(providers_app, name="providers")
app.add_typer(auth_app, name="auth")
app.add_typer(litellm_app, name="litellm")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(doctor_app, name="doctor")
app.add_typer(schedule_app, name="schedule")


@app.command("init")
def init_cmd(
    config: Path | None = typer.Option(
        None, "--config", "-c",
        help="Write the default config.toml to this path instead of the default location.",
    ),
) -> None:
    """Bootstrap a fresh setup: write default config, check secrets, show next steps.

    Never overwrites an existing config file. Secrets stay in the keychain;
    this command only reports what's missing and shows the exact commands
    to store each key.
    """
    from rich.table import Table

    from model_manager import config as config_mod
    from model_manager.domain import auth, providers

    # 1. Config file (create only when absent)
    if config is not None:
        target = config
        existed = target.exists()
    else:
        existing = config_mod.find_config(None)
        if existing is not None:
            existed, target = True, existing
        else:
            import platformdirs

            target = Path(platformdirs.user_config_dir("model-manager")) / "config.toml"
            existed = False

    if existed:
        console.print(f"Config already exists at [bold]{target}[/bold]; leaving it untouched.")
        cfg = config_mod.load_config(target if config is not None else None)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        cfg = config_mod.AppConfig()
        config_mod.save_config(cfg, target)
        console.print(f"[green]Created default config at [bold]{target}[/bold][/green]")

    # 2. Data directory (AppConfig ensures it exists on load)
    console.print(f"Data directory ready at [bold]{cfg.data_dir}[/bold]")

    # 3. Secrets: supported providers plus Artificial Analysis
    needed: list[tuple[str, str, str]] = [
        ("ARTIFICIAL_ANALYSIS_API_KEY", "scores fetch", "Artificial Analysis key"),
    ]
    for p in providers.list_providers():
        use = f"{p.name} fetch/scan"
        if p.name == "OpenRouter":
            use += " (optional for fetch)"
        needed.append((p.secret_key, use, f"{p.name} key"))

    table = Table(title="Required API Keys")
    table.add_column("Key", style="cyan")
    table.add_column("Needed for", style="magenta")
    table.add_column("Status", justify="center")
    missing: list[tuple[str, str]] = []
    for key_name, used_for, label in needed:
        present = bool(auth.get_secret(key_name))
        table.add_row(key_name, used_for, "[green]Stored[/green]" if present else "[red]Missing[/red]")
        if not present:
            missing.append((key_name, label))
    console.print(table)

    # 4. Exact commands for every missing key
    if missing:
        console.print("\n[bold]Store each missing key with:[/bold]")
        for key_name, label in missing:
            console.print(f"  model-manager auth set {key_name}=<Add your {label}>")
    else:
        console.print("\n[green]All required keys are stored.[/green]")

    # 5. Next steps
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. model-manager scores fetch && model-manager scores sync")
    console.print("  2. model-manager providers fetch-all")
    console.print("  3. model-manager litellm generate all --dry-run")
    console.print("  4. model-manager schedule install")
