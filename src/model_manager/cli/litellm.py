"""CLI commands for LiteLLM service management."""
from __future__ import annotations

import typer
import yaml
from pathlib import Path
from typing import Optional

from rich.console import Console

from model_manager.config import get_litellm_restart_request_path, load_config
from model_manager.domain import cost_map, yaml_gen
from .common import console

litellm_app = typer.Typer(help="Manage LiteLLM service configuration.")

# Group for config operations
config_app = typer.Typer(help="Inspect and validate LiteLLM configuration.")
litellm_app.add_typer(config_app, name="config")

# Group for cost-map operations
cost_map_app = typer.Typer(help="Build and manage the model cost and context map.")
litellm_app.add_typer(cost_map_app, name="cost-map")

@litellm_app.command("request-restart")
def request_restart(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to custom config.toml"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r", help="Reason for requesting service restart."),
) -> None:
    """Request a restart of the LiteLLM service by logging a request entry."""
    from model_manager.domain import restart

    cfg = load_config(config)
    try:
        record = restart.request_restart(cfg, reason=reason)
        console.print("[green]Successfully logged restart request for LiteLLM service.[/green]")
        console.print(f"File: [cyan]{get_litellm_restart_request_path(cfg)}[/cyan]")
        console.print(f"Timestamp: [bold]{record['timestamp']}[/bold]")
    except Exception as e:
        console.print(f"[red]Error requesting restart: {e}[/red]")
        raise typer.Exit(1)

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
    provider: str | None = typer.Argument(
        None, help="Provider name (nvidia, gemini, ollama, openrouter, huggingface)."
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Override output path for the generated YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the generated YAML to stdout instead of writing to file."),
    all_providers: bool = typer.Option(False, "--all-providers",
        help="Generate config for all configured providers."),
) -> None:
    """Generate a LiteLLM YAML config file for a specific provider.

    Reads models.json and scan results, derives LiteLLM model names from
    the provider_id mappings, and generates one entry per configured API key.

    Models with scan status "unauthorized" are excluded; all others are included.
    """
    cfg = load_config(config)

    if all_providers:
        providers_to_generate = [
            p for p, pc in cfg.providers.items()
            if pc.keys and pc.litellm_prefix
        ]
        if not providers_to_generate:
            console.print("[red]No configured providers found with keys and litellm_prefix.[/red]")
            raise typer.Exit(1)

        errors: list[str] = []
        for prov in providers_to_generate:
            try:
                result = yaml_gen.generate_provider_yaml(
                    cfg, prov,
                    dry_run=dry_run,
                    output_path=output,
                )
                if dry_run and result:
                    console.print(f"[bold]=== {prov} ===[/bold]")
                    console.print(result)
                else:
                    console.print(f"[green]Generated config for [bold]{prov}[/bold][/green]")
            except RuntimeError as e:
                errors.append(f"{prov}: {e}")

        if errors:
            console.print("\n[red]Errors:[/red]")
            for err in errors:
                console.print(f"  [red]- {err}[/red]")
            raise typer.Exit(1)
        return

    if not provider:
        console.print("[red]Error: provider argument or --all-providers is required.[/red]")
        raise typer.Exit(1)

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
        console.print(f"[green]Generated config for [bold]{provider}[/bold][/green]")

@generate_app.command("fallbacks")
def generate_fallbacks(
    config: Path | None = typer.Option(None, "--config", "-c"),
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Override output path for the generated fallbacks YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the generated YAML to stdout instead of writing to file."),
    limit: int = typer.Option(5, "--limit",
        help="Maximum number of fallback entries per model."),
) -> None:
    """Generate the LiteLLM fallbacks YAML from tier tags and provider scans.

    Reads models.json and provider scan results, and for every lens that
    ``generate config`` would include emits a ``fallbacks`` map keyed on each
    provider's full model_name. Fallbacks prioritize the same model on other
    providers (active first, dead last) followed by same-tier models ordered by
    composite score.
    """
    from model_manager.domain import fallbacks

    cfg = load_config(config)
    try:
        result = fallbacks.generate_fallbacks_yaml(
            cfg,
            dry_run=dry_run,
            output_path=output,
            limit=limit,
        )
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if dry_run and result:
        Console(emoji=False, highlight=False).print(result)
    elif not dry_run:
        console.print(f"[green]Generated fallbacks config to "
                      f"[bold]{output or cfg.litellm_fallbacks_path}[/bold][/green]")

@generate_app.command("aliases")
def generate_aliases(
    config: Path | None = typer.Option(None, "--config", "-c",
        help="Path to config TOML file."),
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Override output path for the generated aliases YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the generated YAML to stdout instead of writing to file."),
) -> None:
    """Generate the LiteLLM model_group_alias YAML from tier tags.

    For each tier (tier1, tier2, tier3) emits one alias pointing at the
    best-composite in-tier variant, trying providers in the tier's
    ``[tier_providers.<tier>]`` order first. Tiers without eligible
    variants are omitted.
    """
    from model_manager.domain import model_group_aliases

    cfg = load_config(config)
    try:
        result = model_group_aliases.generate_aliases_yaml(
            cfg,
            dry_run=dry_run,
            output_path=output,
        )
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if dry_run and result:
        Console(emoji=False, highlight=False).print(result)
    elif not dry_run:
        console.print(f"[green]Generated aliases config to "
                      f"[bold]{output or cfg.litellm_aliases_path}[/bold][/green]")

@generate_app.command("router_settings")
def generate_router_settings(
    config: Path | None = typer.Option(None, "--config", "-c",
        help="Path to config TOML file."),
    output: Path | None = typer.Option(None, "--output", "-o",
        help="Override output path for the merged router_settings YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the merged YAML to stdout instead of writing to file."),
    stub: Path | None = typer.Option(None, "--stub",
        help="Override path to the stub router_settings YAML file."),
    from_files: bool = typer.Option(False, "--from-files",
        help="Read fallbacks/aliases from their generated YAML files "
             "instead of regenerating from models.json."),
    limit: int = typer.Option(5, "--limit",
        help="Maximum number of fallback entries per model (ignored with --from-files)."),
) -> None:
    """Merge generated fallbacks + aliases into the stub router_settings file.

    Starts from the stub (routing strategy, retries, hand-managed aliases),
    inserts fallbacks under the router_settings block, and merges generated
    tier1/2/3 entries into model_group_alias (generated wins on collision,
    all other manual aliases are preserved). The stub is never overwritten;
    output always goes to litellm-router_settings.yaml (or --output).
    """
    from model_manager.domain import router_settings as rs_mod

    cfg = load_config(config)
    try:
        result = rs_mod.generate_router_settings_yaml(
            cfg,
            dry_run=dry_run,
            output_path=output,
            limit=limit,
            from_files=from_files,
            stub_path=stub,
        )
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if dry_run and result:
        Console(emoji=False, highlight=False).print(result)
    elif not dry_run:
        console.print(f"[green]Generated router_settings config to "
                      f"[bold]{output or cfg.litellm_router_settings_path}[/bold][/green]")

@generate_app.command("all")
def generate_all(
    config: Path | None = typer.Option(None, "--config", "-c",
        help="Path to config TOML file."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print the generated YAML to stdout instead of writing to file."),
    build_cost_map: bool = typer.Option(False, "--build-cost-map", "--cost-map",
        help="Also build the LiteLLM cost map."),
) -> None:
    """Generate all LiteLLM configuration files (all providers, fallbacks, aliases, and router_settings).

    Optionally builds the model cost map if --build-cost-map / --cost-map flag is set.
    """
    from model_manager.domain import generate_all as gen_all_mod

    cfg = load_config(config)
    result = gen_all_mod.run_generate_all(cfg, dry_run=dry_run)
    errors: list[str] = list(result["errors"])

    if not result["providers"]:
        console.print("[yellow]No configured providers found with keys and litellm_prefix.[/yellow]")
    else:
        for prov in result["providers"]:
            if not result["ok"][prov]:
                continue
            if dry_run and result["outputs"][prov]:
                console.print(f"[bold]=== {prov} ===[/bold]")
                console.print(result["outputs"][prov])
            else:
                console.print(f"[green]Generated config for [bold]{prov}[/bold][/green]")

    if result["ok"].get("fallbacks"):
        if dry_run and result["outputs"]["fallbacks"]:
            console.print("[bold]=== fallbacks ===[/bold]")
            console.print(result["outputs"]["fallbacks"])
        elif not dry_run:
            console.print(f"[green]Generated fallbacks config to "
                          f"[bold]{cfg.litellm_fallbacks_path}[/bold][/green]")

    if result["ok"].get("aliases"):
        if dry_run and result["outputs"]["aliases"]:
            console.print("[bold]=== aliases ===[/bold]")
            Console(emoji=False, highlight=False).print(result["outputs"]["aliases"])
        elif not dry_run:
            console.print(f"[green]Generated aliases config to "
                          f"[bold]{cfg.litellm_aliases_path}[/bold][/green]")

    if result["ok"].get("router_settings"):
        if dry_run and result["outputs"]["router_settings"]:
            console.print("[bold]=== router_settings ===[/bold]")
            Console(emoji=False, highlight=False).print(result["outputs"]["router_settings"])
        elif not dry_run:
            console.print(f"[green]Generated router_settings config to "
                          f"[bold]{cfg.litellm_router_settings_path}[/bold][/green]")

    if build_cost_map:
        if dry_run:
            console.print("[yellow]Skipping cost map build in dry-run mode.[/yellow]")
        else:
            try:
                with console.status("[bold green]Building LiteLLM cost map...") as status:
                    output_path = cost_map.build_local_cost_map(cfg)
                console.print("[green]Successfully built cost map![/green]")
                console.print(f"Output path: [cyan]{output_path}[/cyan]")
            except Exception as e:
                errors.append(f"cost-map: {e}")

    if errors:
        console.print("\n[red]Errors during generation:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(1)

@litellm_app.command("scan")
def litellm_scan(
    config: Path | None = typer.Option(None, "--config", "-c",
        help="Path to config TOML file."),
    base_url: str = typer.Option("http://localhost:4000", "--base-url",
        help="LiteLLM proxy base URL."),
    timeout: int = typer.Option(180, "--timeout",
        help="Per-model request timeout in seconds."),
    max_tokens: int = typer.Option(256, "--max-tokens",
        help="Completion token cap per probe (shared by reasoning + content)."),
    filter_str: str | None = typer.Option(None, "--filter", "-f",
        help="Only scan targets containing this substring."),
    models: str | None = typer.Option(None, "--models",
        help="Comma-separated target list (overrides enumeration)."),
    skip_aliases: bool = typer.Option(False, "--skip-aliases",
        help="Skip alias targets; scan served model names only."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="List targets and proxy drift without probing."),
    json_output: bool = typer.Option(False, "--json",
        help="Emit scan records as JSON instead of a table."),
) -> None:
    """Probe every model served by the LiteLLM proxy, including aliases.

    Streams a fixed-size completion per target and records status, TTFT,
    and throughput to data_dir/litellm_scan.json.
    """
    from rich.table import Table

    from model_manager.domain import auth, litellm_scan as scan_mod

    cfg = load_config(config)
    api_key = auth.get_secret(scan_mod.LITELLM_API_KEY_NAME)
    if not api_key:
        console.print(
            f"[red]Error: {scan_mod.LITELLM_API_KEY_NAME} not found. "
            f"Store it with: model-manager auth set "
            f"{scan_mod.LITELLM_API_KEY_NAME}=<key>[/red]"
        )
        raise typer.Exit(1)

    try:
        targets = scan_mod.enumerate_targets(cfg)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if models:
        wanted = [m.strip() for m in models.split(",") if m.strip()]
        targets = {
            "models": [m for m in targets["models"] if m in wanted],
            "aliases": [] if skip_aliases else [a for a in targets["aliases"] if a in wanted],
        }
    else:
        if filter_str:
            f = filter_str.lower()
            targets = {
                "models": [m for m in targets["models"] if f in m.lower()],
                "aliases": [a for a in targets["aliases"] if f in a.lower()],
            }
        if skip_aliases:
            targets["aliases"] = []

    all_targets = targets["models"] + targets["aliases"]
    if not all_targets:
        console.print("[yellow]No targets selected.[/yellow]")
        raise typer.Exit(1)

    try:
        served = scan_mod.fetch_proxy_models(base_url, api_key)
        drift = scan_mod.reconcile_targets(targets, served)
    except RuntimeError as e:
        console.print(f"[yellow]Warning: {e} Proceeding with file targets.[/yellow]")
        drift = {"served_count": 0, "target_count": len(all_targets), "unserved": all_targets, "extra_served": []}

    if drift["unserved"]:
        console.print(
            f"[yellow]{len(drift['unserved'])} target(s) not currently served "
            f"by the proxy (restart pending?): "
            f"{', '.join(drift['unserved'][:10])}"
            f"{'...' if len(drift['unserved']) > 10 else ''}[/yellow]"
        )

    if dry_run:
        console.print(f"[bold]Models ({len(targets['models'])}):[/bold] "
                      + (", ".join(targets["models"]) or "(none)"))
        console.print(f"[bold]Aliases ({len(targets['aliases'])}):[/bold] "
                      + (", ".join(targets["aliases"]) or "(none)"))
        return

    records = scan_mod.scan_targets(
        base_url, api_key, all_targets,
        max_tokens=max_tokens, timeout=timeout,
        kinds={**{m: "model" for m in targets["models"]},
               **{a: "alias" for a in targets["aliases"]}},
        on_result=lambda r: console.print(
            f"  {'[green]✓[/green]' if r['status'] == 'up' else '[yellow]?[/yellow]' if r['status'] == 'empty' else '[red]![/red]'} "
            f"{r['model']}: {r['status']}"
            + (f" TTFT {r['ttft_ms']}ms TPS {r['tps']}" if r["status"] == "up" and r["tps"] else "")
            + (f" finish={r['finish_reason']}" if r.get("finish_reason") else "")
        ),
    )
    out_path = scan_mod.save_litellm_scan(cfg, records)
    try:
        from model_manager.domain import blocks as _blocks

        _blocks.record_probe_observations(cfg, records)
    except Exception:
        pass

    if json_output:
        import json as _json

        console.print(_json.dumps(records, indent=2))
    else:
        table = Table(title="LiteLLM Proxy Scan")
        table.add_column("Model", style="cyan")
        table.add_column("Kind", justify="center")
        table.add_column("Status", justify="center")
        table.add_column("TTFT (ms)", justify="right")
        table.add_column("TPS", justify="right")
        table.add_column("TPM est", justify="right")
        table.add_column("Code", justify="center")
        for r in records:
            table.add_row(
                r["model"], r.get("kind", "model"), r["status"],
                str(r["ttft_ms"] or "—"), str(r["tps"] or "—"),
                str(r["tpm_est"] or "—"), str(r["code"] or "—"),
            )
        console.print(table)

    up = sum(1 for r in records if r["status"] == "up")
    empty = sum(1 for r in records if r["status"] == "empty")
    console.print(f"[dim]Scanned {len(records)} targets ({up} up, {empty} empty). Saved to {out_path}[/dim]")
    if up < len(records):
        raise typer.Exit(1)
