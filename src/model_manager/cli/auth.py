"""CLI commands for managing secure API keys."""
from __future__ import annotations

import typer
from pathlib import Path
from rich.table import Table

from model_manager.domain import auth
from .common import console

auth_app = typer.Typer(help="Manage secure API keys in the system keychain.")

@auth_app.command("set")
def auth_set(
    key_name: str,
    value: str,
) -> None:
    """Store an API key in the system keychain.

    Example: model-manager auth set OPENROUTER_API_KEY=sk-or-xxx
    """
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
    tracked_keys = ["OPENROUTER_API_KEY", "ARTIFICIAL_ANALYSIS_API_KEY", "NVIDIA_API_KEY", "OLLAMA_API_KEY", "GEMINI_API_KEY", "HF_TOKEN", "LITELLM_MASTER_KEY"]

    table = Table(title="Stored Secrets")
    table.add_column("Key", style="cyan")
    table.add_column("Status", style="magenta")

    for k in tracked_keys:
        val = auth.get_secret(k)
        status = "[green]Stored[/green]" if val else "[red]Missing[/red]"
        table.add_row(k, status)

    console.print(table)

@auth_app.command("update-env")
def auth_update_env(
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Write the service env file to this path instead of the default."),
) -> None:
    """Export keychain keys to the scheduled service's env file.

    Re-run after changing keys so scheduled runs pick up the new values.
    """
    from model_manager.domain import service_env as service_env_mod

    target = output or service_env_mod.default_service_env_path()
    try:
        count = service_env_mod.export_keyring_to_env(target)
    except Exception as e:
        console.print(f"[red]Error exporting keychain: {e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Wrote {count} keys to [bold]{target}[/bold][/green]")
