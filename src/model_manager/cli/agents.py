"""CLI commands for autonomous agent runtime models."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.panel import Panel
from rich.table import Table

from model_manager.cli.common import console, load_config
from model_manager.domain import agents as agents_mod

agents_app = typer.Typer(
    help="Manage and interact with autonomous agent runtime models.",
    no_args_is_help=True,
)


@agents_app.command("list")
def list_cmd(
    all_models: bool = typer.Option(
        False, "--all", "-a",
        help="Include unmapped candidate agent models discovered in provider caches.",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit output as JSON.",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to custom config.toml",
    ),
) -> None:
    """List available agent runtime models."""
    cfg = load_config(config)
    agent_list = agents_mod.list_agents(cfg, include_unmapped=all_models)

    if json_output:
        console.print(json.dumps(agent_list, indent=2))
        return

    if not agent_list:
        console.print("[yellow]No agent models found. Add one with 'models add' or tag a variant with 'agent'.[/yellow]")
        return

    table = Table(title="Available Agent Models")
    table.add_column("Agent Alias", style="cyan bold")
    table.add_column("Provider Target", style="magenta")
    table.add_column("Protocol / Endpoint", style="blue")
    table.add_column("Limits", style="dim")
    table.add_column("Status", justify="center")

    for a in agent_list:
        limits = a.get("limits") or {}
        limit_str = []
        if limits.get("rpm"):
            limit_str.append(f"RPM:{limits['rpm']}")
        if limits.get("tpm"):
            limit_str.append(f"TPM:{limits['tpm']}")
        limit_display = " | ".join(limit_str) or "-"

        status = a.get("status", "Unknown")
        if status in ("Good", "Configured"):
            status_display = f"[green]{status}[/green]"
        elif status == "Discovered":
            status_display = f"[yellow]{status}[/yellow]"
        else:
            status_display = f"[red]{status}[/red]"

        table.add_row(
            a["alias"],
            a["target"],
            a["endpoint"],
            limit_display,
            status_display,
        )

    console.print(table)


@agents_app.command("run")
def run_cmd(
    prompt: Optional[str] = typer.Argument(
        None,
        help="Instruction or prompt for the agent. If omitted, reads from stdin.",
    ),
    agent: str = typer.Option(
        "antigravity", "--agent", "-a",
        help="Target agent alias or model ID.",
    ),
    direct: bool = typer.Option(
        False, "--direct",
        help="Send directly to Google AI Studio Interactions API (bypassing LiteLLM proxy).",
    ),
    tools: Optional[List[str]] = typer.Option(
        None, "--tools", "-t",
        help="Tools enabled in the sandbox substrate (e.g. code_execution, google_search). Repeatable.",
    ),
    env: str = typer.Option(
        "remote", "--env", "-e",
        help="Execution environment substrate (remote or local).",
    ),
    session: Optional[str] = typer.Option(
        None, "--session", "-s",
        help="Previous interaction_id to continue a multi-turn sandbox session.",
    ),
    timeout: int = typer.Option(
        180, "--timeout",
        help="Timeout in seconds for agent execution.",
    ),
    key: Optional[str] = typer.Option(
        None, "--key", "-k",
        help="Optional API key override.",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url",
        help="LiteLLM proxy base URL (defaults to http://localhost:4000).",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit raw interaction response as JSON.",
    ),
    debug: bool = typer.Option(
        False, "--debug",
        help="Display wire protocol request and response details.",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to custom config.toml",
    ),
) -> None:
    """Send an instruction to an agent runtime via the Interactions API."""
    cfg = load_config(config)

    # Read from stdin if prompt not provided
    input_text = prompt
    if input_text is None:
        if not sys.stdin.isatty():
            input_text = sys.stdin.read().strip()
        if not input_text:
            console.print("[red]Error: Prompt cannot be empty. Pass prompt as an argument or pipe via stdin.[/red]")
            raise typer.Exit(1)

    with console.status(f"[bold cyan]Submitting task to {agent} via {'direct Google API' if direct else 'LiteLLM proxy'}...[/bold cyan]"):
        result = agents_mod.run_interaction(
            cfg,
            agent=agent,
            prompt=input_text,
            tools=tools or ["code_execution"],
            environment=env,
            previous_interaction_id=session,
            direct=direct,
            base_url=base_url,
            api_key=key,
            timeout=timeout,
            debug=debug,
        )

    if json_output:
        console.print(json.dumps(result, indent=2))
        if not result.get("success"):
            raise typer.Exit(1)
        return

    if debug and result.get("debug"):
        console.print("\n[bold]Wire Debug Details:[/bold]")
        console.print(json.dumps(result["debug"], indent=2))

    if not result.get("success"):
        console.print(f"\n[red]Interaction Failed (HTTP {result.get('status_code')})[/red]")
        console.print(f"[red]Error: {result.get('error')}[/red]")
        if result.get("detail"):
            console.print(Panel(json.dumps(result["detail"], indent=2), title="Error Detail", border_style="red"))
        raise typer.Exit(1)

    # Success output
    interaction_id = result.get("interaction_id") or "(none)"
    latency = result.get("latency_ms", 0)
    console.print(f"\n[green]✓ Interaction Completed[/green] in [bold]{latency}ms[/bold] "
                  f"(Session: [cyan]{interaction_id}[/cyan])")

    outputs = result.get("outputs", [])
    if outputs:
        for idx, out in enumerate(outputs):
            if isinstance(out, dict):
                text = out.get("text") or out.get("content") or json.dumps(out, indent=2)
                console.print(Panel(text, title=f"Output {idx+1}", border_style="green"))
            else:
                console.print(Panel(str(out), title=f"Output {idx+1}", border_style="green"))
    else:
        raw_res = result.get("raw_response", {})
        console.print(Panel(json.dumps(raw_res, indent=2), title="Agent Response", border_style="green"))


# Register alias 'interact' pointing to run_cmd
agents_app.command("interact", hidden=True)(run_cmd)
