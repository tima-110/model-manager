"""Shared implementation of `litellm generate all` for the CLI and scheduler.

Generates every LiteLLM configuration file in dependency order: provider
configs, fallbacks, aliases, then the merged router_settings. Add future
generation steps here once and both callers pick them up.
"""
from __future__ import annotations

from model_manager.config import AppConfig
from model_manager.domain import fallbacks, model_group_aliases
from model_manager.domain import router_settings as rs_mod
from model_manager.domain import yaml_gen


def run_generate_all(config: AppConfig, *, dry_run: bool = False) -> dict:
    """Generate all LiteLLM configuration files.

    Args:
        config: Application configuration with output paths.
        dry_run: When True, build each document and return its YAML
            text instead of writing files.

    Returns:
        dict with:
            providers: provider names attempted (have keys + litellm_prefix).
            outputs: section name -> YAML text on dry_run, else None.
            ok: section name -> True when that section succeeded.
            steps: human-readable per-section outcome lines.
            errors: human-readable per-section error lines.
    """
    steps: list[str] = []
    errors: list[str] = []
    outputs: dict[str, str | None] = {}
    ok: dict[str, bool] = {}

    providers_to_generate = [
        p for p, pc in config.providers.items()
        if pc.keys and pc.litellm_prefix
    ]

    for prov in providers_to_generate:
        try:
            outputs[prov] = yaml_gen.generate_provider_yaml(config, prov, dry_run=dry_run)
            ok[prov] = True
            steps.append(f"generate config ({prov}): success")
        except RuntimeError as e:
            outputs[prov] = None
            ok[prov] = False
            errors.append(f"provider '{prov}': {e}")

    for section, func in (
        ("fallbacks", fallbacks.generate_fallbacks_yaml),
        ("aliases", model_group_aliases.generate_aliases_yaml),
        ("router_settings", rs_mod.generate_router_settings_yaml),
    ):
        try:
            outputs[section] = func(config, dry_run=dry_run)
            ok[section] = True
            steps.append(f"generate {section}: success")
        except RuntimeError as e:
            outputs[section] = None
            ok[section] = False
            errors.append(f"{section}: {e}")

    return {
        "providers": providers_to_generate,
        "outputs": outputs,
        "ok": ok,
        "steps": steps,
        "errors": errors,
    }
