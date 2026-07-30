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
