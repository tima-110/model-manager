"""Generate LiteLLM YAML config from models.json and scan results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from model_manager.config import AppConfig

SCAN_FILES: dict[str, str] = {
    "nvidia": "nvidia_scan.json",
    "gemini": "gemini_scan.json",
    "ollama": "ollama_scan.json",
    "openrouter": "openrouter_scan.json",
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _load_scan_results(config: AppConfig, provider: str) -> dict[str, str]:
    """Load scan results for *provider* and return a dict of provider_id → assessment."""
    scan_file = config.data_dir / SCAN_FILES.get(provider, f"{provider}_scan.json")
    data = _load_json(scan_file)
    models = data.get("models", {})
    assessments: dict[str, str] = {}
    for pid, info in models.items():
        if isinstance(info, dict):
            summary = info.get("summary", {}) or {}
            assessments[pid] = summary.get("assessment", "unknown").lower()
        else:
            assessments[pid] = "unknown"
    return assessments


def _iter_provider_ids(
    provider_ids: dict[str, Any] | list[str],
) -> list[str]:
    """Yield individual provider_id strings regardless of container type."""
    if isinstance(provider_ids, list):
        return list(provider_ids)
    return list(provider_ids.keys())


def _derive_model_name(litellm_prefix: str, provider_id: str) -> str:
    """Derive the LiteLLM model_name from the last segment of provider_id."""
    last_segment = provider_id.split("/")[-1]
    return f"{litellm_prefix}/{last_segment}"


def _derive_model(litellm_prefix: str, provider_id: str) -> str:
    """Derive the full LiteLLM model string."""
    return f"{litellm_prefix}/{provider_id}"


def generate_provider_yaml(
    config: AppConfig,
    provider: str,
    *,
    dry_run: bool = False,
    output_path: Path | None = None,
) -> str | None:
    """Generate a LiteLLM YAML config for *provider*.

    Reads models.json, crosses provider_id entries against scan results,
    filters out unauthorized models, and produces a ``model_list`` YAML
    with entries replicated for each configured API key.

    Returns the YAML string when *dry_run* is True, or writes to the output
    path (from config or *output_path* override) and returns None.
    """
    # Resolve provider config
    provider_cfg = config.providers.get(provider)
    if not provider_cfg:
        raise RuntimeError(
            f"No output configuration found for provider '{provider}'. "
            f"Add a [providers.{provider}] section to config.toml with keys, "
            "litellm_prefix, and output_path."
        )
    if not provider_cfg.keys:
        raise RuntimeError(
            f"No API keys configured for provider '{provider}'. "
            f"Add keys = [...] to the [providers.{provider}] section."
        )
    if not provider_cfg.litellm_prefix:
        raise RuntimeError(
            f"No litellm_prefix configured for provider '{provider}'."
        )

    # Load models.json
    models_path = config.data_dir / "models.json"
    models_data = _load_json(models_path)
    all_models = models_data.get("models", {})

    # Load scan results (best-effort — missing file = include all)
    scan_assessments = _load_scan_results(config, provider)

    entries: list[dict[str, Any]] = []

    for model_id, model_data in all_models.items():
        variants = model_data.get("variants", {})
        for variant_id, variant_data in variants.items():
            # Skip variants excluded from LiteLLM config
            if variant_data.get("include_in_litellm") is False:
                continue
            prov_map = variant_data.get("provider_ids", {})
            # provider key may be lowercased in models.json
            provider_ids_map = prov_map.get(provider) or prov_map.get(provider.capitalize())
            if not provider_ids_map:
                continue

            for pid in _iter_provider_ids(provider_ids_map):
                # Check scan status — skip unauthorized
                assessment = scan_assessments.get(pid)
                if assessment == "unauthorized":
                    continue

                # Derive model names
                model_name = _derive_model_name(provider_cfg.litellm_prefix, pid)
                model = _derive_model(provider_cfg.litellm_prefix, pid)

                # Replicate for each API key
                for key in provider_cfg.keys:
                    params: dict[str, Any] = {
                        "model": model,
                        "api_key": f"os.environ/{key}",
                    }
                    if provider_cfg.rpm is not None:
                        params["rpm"] = provider_cfg.rpm
                    if provider_cfg.api_base is not None:
                        params["api_base"] = provider_cfg.api_base

                    entries.append({
                        "model_name": model_name,
                        "litellm_params": params,
                    })

    if not entries:
        raise RuntimeError(
            f"No entries generated for provider '{provider}'. "
            "Check that models.json has mapped provider_ids for this provider."
        )

    yaml_doc = yaml.safe_dump(
        {"model_list": entries},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    if dry_run:
        return yaml_doc

    out_path = output_path or provider_cfg.output_path
    if not out_path:
        raise RuntimeError(
            f"No output_path configured for provider '{provider}' "
            "and no --output argument provided."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file if present
    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None