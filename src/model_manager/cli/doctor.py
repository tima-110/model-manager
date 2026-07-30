"""CLI commands for diagnosing tool health and environment."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from importlib.metadata import version as pkg_version
from pathlib import Path

import typer
import yaml
from rich.table import Table

from model_manager.config import (
    find_config,
    get_free_models_path,
    get_gemini_models_path,
    get_litellm_cost_overrides_path,
    get_models_path,
    get_nvidia_models_path,
    get_ollama_models_path,
    get_raw_scores_path,
    get_scores_path,
    load_config,
)
from model_manager.domain import auth, providers
from .common import console

doctor_app = typer.Typer(help="Diagnose tool health and environment.")


def _check(rows: list, check: str, status: str, detail: str) -> None:
    """Append a (check, status, detail) row to the results list."""
    rows.append((check, status, detail))


def _status_tag(status: str) -> str:
    return {
        "pass": "[green]PASS[/green]",
        "fail": "[red]FAIL[/red]",
        "warn": "[yellow]WARN[/yellow]",
        "info": "[blue]INFO[/blue]",
    }.get(status, status)


@doctor_app.callback(invoke_without_command=True)
def main(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to custom config.toml"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show additional detail (e.g. file sizes)."),
) -> None:
    """Run diagnostic checks and print a health report."""
    rows: list[tuple[str, str, str]] = []
    has_failure = False

    # 1. Version checks
    try:
        mm_ver = pkg_version("model-manager")
    except Exception:
        mm_ver = "unknown"
    _check(rows, "Python version", "info", sys.version.split()[0])
    _check(rows, "model-manager version", "info", mm_ver)

    # 2. Config
    cfg_path = find_config(config)
    if cfg_path is None:
        _check(rows, "Config file", "warn", "No config file found (using defaults)")
        cfg = load_config(config)
    else:
        _check(rows, "Config file", "info", str(cfg_path))
        try:
            cfg_path.read_bytes()
            with open(cfg_path, "rb") as f:
                tomllib.load(f)
            _check(rows, "Config parse", "pass", "Valid TOML")
        except tomllib.TOMLDecodeError as e:
            _check(rows, "Config parse", "fail", f"Invalid TOML: {e}")
            has_failure = True
        except PermissionError:
            _check(rows, "Config parse", "fail", "Permission denied")
            has_failure = True
        cfg = load_config(config)

    # 3. Data directory
    data_dir = cfg.data_dir
    _check(rows, "Data directory", "info", str(data_dir))
    if not data_dir.exists():
        _check(rows, "Data dir exists", "warn", "Directory does not exist")
    else:
        _check(rows, "Data dir exists", "pass", "Exists")
        if not os.access(data_dir, os.W_OK):
            _check(rows, "Data dir writable", "fail", "Not writable")
            has_failure = True
        else:
            _check(rows, "Data dir writable", "pass", "Writable")

    # 4. Data files
    data_files = [
        ("Scores", get_scores_path(cfg)),
        ("Models", get_models_path(cfg)),
        ("Raw AA response", get_raw_scores_path(cfg)),
        ("OpenRouter free models", get_free_models_path(cfg)),
        ("NVIDIA models", get_nvidia_models_path(cfg)),
        ("Ollama models", get_ollama_models_path(cfg)),
        ("Gemini models", get_gemini_models_path(cfg)),
        ("Cost overrides", get_litellm_cost_overrides_path(cfg)),
    ]
    for label, path in data_files:
        has_failure = _check_data_file(rows, label, path, verbose, has_failure)

    # 5. Secrets
    for provider in providers.list_providers():
        secret = auth.get_secret(provider.secret_key)
        if secret:
            _check(rows, f"Secret: {provider.secret_key}", "pass", "Found")
        else:
            _check(rows, f"Secret: {provider.secret_key}", "warn", "Not found (env or keychain)")

    # 6. Provider cache files
    for provider in providers.list_providers():
        cache_path = provider.path_fn(cfg)
        label = f"Cache: {provider.name}"
        if cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text())
                models_list = raw.get("models", [])
                count = len(models_list)
                if count > 0:
                    _check(rows, label, "pass", f"{count} models cached")
                else:
                    _check(rows, label, "warn", "Cache file is empty")
            except (json.JSONDecodeError, OSError) as e:
                _check(rows, label, "fail", f"Corrupt: {e}")
                has_failure = True
        else:
            _check(rows, label, "warn", "No cache file")

    # 7. LiteLLM service directory
    litellm_dir = cfg.litellm_service_dir
    _check(rows, "LiteLLM service dir", "info", str(litellm_dir))
    if litellm_dir.exists():
        _check(rows, "LiteLLM dir exists", "pass", "Exists")
    else:
        _check(rows, "LiteLLM dir exists", "warn", "Directory does not exist")

    # 8. LiteLLM config file
    litellm_config = cfg.litellm_config_path
    _check(rows, "LiteLLM config file", "info", str(litellm_config))
    if litellm_config.exists():
        _check(rows, "LiteLLM config exists", "pass", "Exists")
        try:
            yaml.safe_load(litellm_config.read_text())
            _check(rows, "LiteLLM config YAML", "pass", "Valid YAML")
        except yaml.YAMLError as e:
            _check(rows, "LiteLLM config YAML", "fail", f"Invalid YAML: {e}")
            has_failure = True
        except PermissionError:
            _check(rows, "LiteLLM config YAML", "fail", "Permission denied")
            has_failure = True
    else:
        _check(rows, "LiteLLM config exists", "warn", "File not found")

    # --- Print results ---
    table = Table(title="model-manager Health Report")
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Detail")

    for check, status, detail in rows:
        table.add_row(check, _status_tag(status), detail)

    console.print(table)

    if has_failure:
        raise typer.Exit(1)


def _check_data_file(
    rows: list,
    label: str,
    path: Path,
    verbose: bool,
    has_failure: bool,
) -> bool:
    """Append rows for a single data file check. Returns updated has_failure."""
    if not path.exists():
        _check(rows, f"Data: {label}", "info", "Not present")
        return has_failure
    try:
        content = path.read_bytes()
        json.loads(content)
        detail = f"{len(content):,} bytes" if verbose else "Valid JSON"
        _check(rows, f"Data: {label}", "pass", detail)
    except json.JSONDecodeError as e:
        _check(rows, f"Data: {label}", "fail", f"Invalid JSON: {e}")
        has_failure = True
    except OSError as e:
        _check(rows, f"Data: {label}", "fail", str(e))
        has_failure = True
    return has_failure