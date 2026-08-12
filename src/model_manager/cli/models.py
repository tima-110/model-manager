"""CLI commands for managing conceptual models."""
from __future__ import annotations

import json
import typer
from datetime import datetime
from pathlib import Path
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from model_manager.config import load_config, get_free_models_path, get_nvidia_models_path, get_ollama_models_path
from model_manager.domain import models, scores, aliases, discovery, auth, tags
from .common import console, _run_discovery_cli_workflow

models_app = typer.Typer(help="Manage the conceptual model library.")

# Variant sub-commands
variant_app = typer.Typer(help="Manage model variants.")
models_app.add_typer(variant_app, name="variant")

# Tag sub-commands
tag_app = typer.Typer(help="Tag model variants (auto tiers + manual tags).")
models_app.add_typer(tag_app, name="tag")

@variant_app.command("update")
def variant_update(
    model: str,
    variant: str,
    litellm_disable: bool = typer.Option(False, "--litellm-disable",
        help="Exclude this variant from LiteLLM config generation."),
    litellm_enable: bool = typer.Option(False, "--litellm-enable",
        help="Re-include this variant in LiteLLM config generation."),
    litellm_disable_provider: str | None = typer.Option(None, "--litellm-disable-provider",
        help="Exclude a specific provider for this variant (e.g. openrouter)."),
    litellm_enable_provider: str | None = typer.Option(None, "--litellm-enable-provider",
        help="Re-include a specific provider for this variant."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Update settings for a model variant.

    Examples:

      model-manager model variant update gpt-4 8b-q4 --litellm-disable

      model-manager model variant update gpt-4 8b-q4 --litellm-disable-provider openrouter
    """
    cfg = load_config(config)

    if litellm_disable and litellm_enable:
        console.print("[red]Error: Cannot use --litellm-disable and --litellm-enable together.[/red]")
        raise typer.Exit(1)

    if litellm_disable_provider and litellm_enable_provider:
        console.print("[red]Error: Cannot use --litellm-disable-provider and --litellm-enable-provider together.[/red]")
        raise typer.Exit(1)

    include_in_litellm: bool | None = None
    if litellm_disable:
        include_in_litellm = False
    elif litellm_enable:
        include_in_litellm = True

    include_in_litellm_provider: bool | None = None
    if litellm_disable_provider:
        include_in_litellm_provider = False
    elif litellm_enable_provider:
        include_in_litellm_provider = True

    if models.update_variant(
        cfg, model, variant,
        include_in_litellm=include_in_litellm,
        provider=litellm_disable_provider or litellm_enable_provider,
        include_in_litellm_provider=include_in_litellm_provider,
    ):
        if include_in_litellm is False:
            console.print(f"[green]Variant [bold]{model}/{variant}[/bold] excluded from LiteLLM config.[/green]")
        elif include_in_litellm is True:
            console.print(f"[green]Variant [bold]{model}/{variant}[/bold] included in LiteLLM config.[/green]")
        elif include_in_litellm_provider is False:
            console.print(f"[green]Provider [bold]{litellm_disable_provider}[/bold] for [bold]{model}/{variant}[/bold] excluded from LiteLLM config.[/green]")
        elif include_in_litellm_provider is True:
            console.print(f"[green]Provider [bold]{litellm_enable_provider}[/bold] for [bold]{model}/{variant}[/bold] included in LiteLLM config.[/green]")
        else:
            console.print(f"[green]Variant [bold]{model}/{variant}[/bold] updated.[/green]")
    else:
        console.print(f"[red]Error: Variant [bold]{model}/{variant}[/bold] not found.[/red]")
        raise typer.Exit(1)

@tag_app.command("tier")
def tag_tier(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview tier assignments without writing to models.json."),
    t1_ratio: float | None = typer.Option(None, "--t1-ratio", help="Override Tier 1 cutoff as a fraction of the top composite score."),
    t2_ratio: float | None = typer.Option(None, "--t2-ratio", help="Override Tier 2 cutoff as a fraction of the top composite score."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Automatically assign tier tags (tier-1/2/3) to all scored model variants.

    Tiers are relative to the best composite score in the library (avg of
    available intelligence/coding/agentic indices), so they track scores as
    models improve.
    """
    cfg = load_config(config)
    t1 = t1_ratio if t1_ratio is not None else cfg.tags.tier1_min_ratio
    t2 = t2_ratio if t2_ratio is not None else cfg.tags.tier2_min_ratio

    if not 0 < t2 < t1 < 1:
        console.print("[red]Error: ratios must satisfy 0 < t2-ratio < t1-ratio < 1.[/red]")
        raise typer.Exit(2)

    data = models.storage.load_models_data(cfg)
    tiers = tags.compute_tiers(data, t1_ratio=t1, t2_ratio=t2)

    if not tiers:
        console.print("[yellow]No scored variants found in models.json. Run 'scores sync' first.[/yellow]")
        raise typer.Exit(1)

    tier_color = {"tier-1": "green", "tier-2": "yellow", "tier-3": "red"}
    rows = sorted(tiers.items())
    if json_output:
        console.print(json.dumps({"tier1_ratio": t1, "tier2_ratio": t2, "variants": dict(rows)}, indent=2))
    else:
        table = Table(title=f"Tier Assignments (T1 >= {t1:.0%} of leader, T2 >= {t2:.0%})")
        table.add_column("Model / Variant", style="cyan")
        table.add_column("Tier", justify="center")
        for key, tier in rows:
            color = tier_color.get(tier, "white")
            table.add_row(key, f"[{color}]{tier}[/{color}]")
        console.print(table)

    if not dry_run:
        updated, _, _ = tags.assign_tier_tags(cfg, t1_ratio=t1, t2_ratio=t2)
        if updated:
            console.print(f"[green]Assigned/updated tier tags for {updated} variants.[/green]")
        else:
            console.print("[dim]No changes; tier tags already up to date.[/dim]")

@tag_app.command("list")
def tag_list(
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List current tags assigned to model variants."""
    cfg = load_config(config)
    tagged = tags.list_tags(cfg)

    if json_output:
        console.print(json.dumps(tagged, indent=2))
        return

    if not tagged:
        console.print("[yellow]No tags assigned yet. Run 'models tag tier' to auto-tag.[/yellow]")
        return

    table = Table(title="Variant Tags")
    table.add_column("Model / Variant", style="cyan")
    table.add_column("Tags", style="magenta")
    for key, tag_list in sorted(tagged.items()):
        table.add_row(key, ", ".join(f"[bold]{t}[/bold]" for t in tag_list))
    console.print(table)

@tag_app.command("set")
def tag_set(
    model: str,
    variant: str,
    tag: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Manually add a tag to a model variant."""
    cfg = load_config(config)
    if not tags.set_tag(cfg, model, variant, tag):
        console.print(f"[red]Error: Variant [bold]{model}/{variant}[/bold] not found.[/red]")
        raise typer.Exit(1)
    if json_output:
        console.print(json.dumps({"status": "success", "model": model, "variant": variant, "tag": tag}, indent=2))
    else:
        console.print(f"[green]Tagged [bold]{model}/{variant}[/bold] with [bold]{tag}[/bold].[/green]")

@tag_app.command("remove")
def tag_remove(
    model: str,
    variant: str,
    tag: str,
    config: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a tag from a model variant."""
    cfg = load_config(config)
    if not tags.remove_tag(cfg, model, variant, tag):
        console.print(f"[red]Error: Variant [bold]{model}/{variant}[/bold] not found.[/red]")
        raise typer.Exit(1)
    if json_output:
        console.print(json.dumps({"status": "success", "model": model, "variant": variant, "tag": tag}, indent=2))
    else:
        console.print(f"[green]Removed [bold]{tag}[/bold] from [bold]{model}/{variant}[/bold].[/green]")

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
    table.add_column("Tier", justify="center")
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
            table.add_row(model_display, "[dim]no variants[/dim]", "-", "-", "-", "-", "-", "-")
            last_model = mid
            continue

        for vid, v_info in variants.items():
            excluded = v_info.get("include_in_litellm") is False
            exclude_mark = " [red](excluded)[/red]" if excluded else ""

            tier = next((t for t in v_info.get("tags", []) if t.startswith("tier-")), "")

            score_str = ""
            slug = v_info.get("aa_slug")
            if slug and slug in all_scores:
                s = all_scores[slug].get("scores", {})
                metrics = [
                    ("I", s.get("intelligence")),
                    ("C", s.get("coding")),
                    ("A", s.get("agentic")),
                ]
                scores_list = [f"{label}: {val}" for label, val in metrics if val is not None]
                if scores_list:
                    score_str = f" ({', '.join(scores_list)})"

            provider_ids = v_info.get("provider_ids", {})
            if not provider_ids:
                model_display = mid if mid != last_model else ""
                variant_display = f"{vid}{score_str}{exclude_mark}" if (vid != last_variant or mid != last_model) else ""
                tier_display = tier if variant_display else "-"
                table.add_row(model_display, variant_display, tier_display, "-", "-", "-", "-", "-")
                last_model = mid
                last_variant = vid
                continue

            for prov, pids in provider_ids.items():
                if prov.startswith("include_"):
                    continue
                if not isinstance(pids, dict):
                    model_display = mid if mid != last_model else ""
                    variant_display = f"{vid}{score_str}{exclude_mark}" if (vid != last_variant or mid != last_model) else ""
                    tier_display = tier if variant_display else "-"
                    table.add_row(model_display, variant_display, tier_display, prov, "[red]Invalid data[/red]", "-", "-", "-")
                    last_model = mid
                    last_variant = vid
                    continue

                prov_excluded = pids.get("include_in_litellm") is False
                prov_exclude_mark = " [red](prov excluded)[/red]" if prov_excluded else ""

                for pid, scan_data in pids.items():
                    if pid.startswith("include_"):
                        continue
                    model_display = mid if mid != last_model else ""
                    variant_display = f"{vid}{score_str}{exclude_mark}{prov_exclude_mark}" if (vid != last_variant or mid != last_model) else ""
                    tier_display = tier if variant_display else "-"

                    status = scan_data.get("assessment", "Unknown")
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
                        tier_display,
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
        models.add_model(cfg, model_id)
        model_res = models.resolve_model(model_id, cfg)

    default_variant = model_res["default_variant"]

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
                    candidates = models.search_aa_candidates(cfg, model_id)
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

    if string_matches:
        console.print("\n[bold]Suggested Matches (via String Matching):[/bold]")
        variants = [v["variant_id"] for v in model_res["variants"]]

        for m in string_matches:
            score_str = f" (score: {m['score']:.2f})"
            console.print(f" - {m['provider']}: {m['provider_id']}{score_str}")

            if yolo:
                var = variants[0] if variants else "standard"
                aliases.add_alias(cfg, model_id, m["provider"], m["provider_id"], var)
                console.print(f"   [dim]Auto-mapped to {var}...[/dim]")
            else:
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
