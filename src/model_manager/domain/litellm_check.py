"""Read-only integrity validation for generated LiteLLM YAML configs.

Checks every generated file (existence + parse), include resolution,
alias/fallback cross-references against registered model names, and
fallback DAG validity — reusing the generation-side validators. Used by
``litellm config check`` and as a restart gate in the schedule pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from model_manager.config import AppConfig


def _load_yaml_doc(path: Path) -> tuple[dict | None, str | None]:
    """Return (doc, error). Missing/unreadable/invalid files yield an error."""
    if not path.exists():
        return None, "File not found"
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return None, f"Invalid YAML: {e}"
    except PermissionError:
        return None, "Permission denied"
    except OSError as e:
        return None, str(e)
    if not isinstance(doc, dict):
        return None, "Expected a YAML mapping at top level"
    return doc, None


def check_litellm_configs(config: AppConfig) -> dict[str, Any]:
    """Validate all LiteLLM configs. Returns {"checks": [...], "errors": [...]}}.

    Each check is {"file": str, "check": str, "ok": bool, "detail": str}.
    Never raises for file problems; unexpected internal errors propagate.
    """
    from model_manager.domain import fallbacks as fallbacks_mod
    from model_manager.domain.router_settings import extract_alias_map, extract_fallbacks

    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def record(path: Path, check: str, ok: bool, detail: str = "") -> None:
        checks.append({"file": str(path), "check": check, "ok": ok, "detail": detail})
        if not ok:
            errors.append(f"{path}: {check}: {detail or 'failed'}")

    # 1. Master config: exists + parses + includes resolve
    master = config.litellm_config_path
    doc, err = _load_yaml_doc(master)
    record(master, "exists+parse", err is None, err or "Valid YAML")
    if doc is None:
        return {"checks": checks, "errors": errors}
    base_dir = master.parent
    for included in doc.get("include") or []:
        inc_path = base_dir / included
        inc_doc, inc_err = _load_yaml_doc(inc_path)
        record(inc_path, "include resolves+parses", inc_err is None, inc_err or "OK")
        if inc_doc is None:
            continue
        if "model_list" in inc_doc:
            entries = inc_doc.get("model_list")
            if not isinstance(entries, list) or not entries:
                record(inc_path, "model_list non-empty", False, "missing or empty")
            else:
                bad = [i for i, e in enumerate(entries)
                       if not isinstance(e, dict) or not e.get("model_name")]
                if bad:
                    record(inc_path, "model_list entries", False, f"entries without model_name at {bad[:5]}")
                else:
                    record(inc_path, "model_list entries", True, f"{len(entries)} entries")
        elif "router_settings" in inc_doc:
            record(inc_path, "include kind", True, "router_settings include (checked below)")
        else:
            record(inc_path, "include kind", False, "unrecognized shape (no model_list/router_settings)")

    # 2. Registered names (model_lists + alias keys/targets)
    try:
        valid_names = fallbacks_mod.collect_valid_model_names(config)
    except Exception as e:
        record(master, "collect model names", False, str(e))
        return {"checks": checks, "errors": errors}

    # 3. Aliases file: targets must be registered
    aliases_doc, aliases_err = _load_yaml_doc(config.litellm_aliases_path)
    record(config.litellm_aliases_path, "exists+parse",
           aliases_err is None, aliases_err or "Valid YAML")
    if aliases_doc is not None:
        try:
            alias_map = extract_alias_map(aliases_doc)
            unknown = sorted(k for k in alias_map.values() if k not in valid_names)
            record(config.litellm_aliases_path, "alias targets registered",
                   not unknown, f"unknown: {unknown[:5]}" if unknown else f"{len(alias_map)} aliases")
        except RuntimeError as e:
            record(config.litellm_aliases_path, "alias map shape", False, str(e))

    # 4. Fallbacks file: DAG validity + registered references
    fb_doc, fb_err = _load_yaml_doc(config.litellm_fallbacks_path)
    record(config.litellm_fallbacks_path, "exists+parse",
           fb_err is None, fb_err or "Valid YAML")
    if fb_doc is not None:
        try:
            fb_list = extract_fallbacks(fb_doc)
            report = fallbacks_mod.validate_fallbacks(fb_list, valid_names)
            problems = (
                [f"unknown: {sorted(report['unknown_names'])[:5]}"] if report["unknown_names"] else []
            ) + (
                [f"malformed idx: {report['malformed'][:5]}"] if report["malformed"] else []
            ) + (
                [f"self-edges: {report['self_edges'][:5]}"] if report["self_edges"] else []
            ) + (
                [f"duplicates: {report['duplicate_subjects'][:5]}"] if report["duplicate_subjects"] else []
            ) + (
                [f"cycles: {len(report['cycles'])}"] if report["cycles"] else []
            )
            record(config.litellm_fallbacks_path, "fallbacks DAG valid",
                   not problems, "; ".join(problems) or "OK")
        except RuntimeError as e:
            record(config.litellm_fallbacks_path, "fallbacks shape", False, str(e))

    # 5. Router settings: stub readable, output parses, merged aliases registered.
    # Hand-managed stub entries legitimately reference names outside the
    # generated sets (e.g. manually added paid models), so the stub's own
    # alias values are admitted alongside registered names here.
    stub_doc, stub_err = _load_yaml_doc(config.litellm_router_settings_stub_path)
    record(config.litellm_router_settings_stub_path, "stub readable",
           stub_err is None, stub_err or "OK")
    stub_alias_values: set[str] = set()
    if stub_doc is not None:
        try:
            stub_alias_values = set(extract_alias_map(stub_doc).values())
        except RuntimeError:
            pass
    merged_allowed = valid_names | stub_alias_values
    rs_doc, rs_err = _load_yaml_doc(config.litellm_router_settings_path)
    record(config.litellm_router_settings_path, "exists+parse",
           rs_err is None, rs_err or "Valid YAML")
    if rs_doc is not None:
        try:
            rs_aliases = extract_alias_map(rs_doc)
            unknown = sorted(k for k in rs_aliases.values() if k not in merged_allowed)
            record(config.litellm_router_settings_path, "alias targets registered",
                   not unknown, f"unknown: {unknown[:5]}" if unknown else f"{len(rs_aliases)} aliases")
            rs_fb = extract_fallbacks(rs_doc)
            rs_report = fallbacks_mod.validate_fallbacks(rs_fb, merged_allowed)
            rs_problems = bool(rs_report["unknown_names"] or rs_report["malformed"]
                               or rs_report["self_edges"] or rs_report["cycles"])
            record(config.litellm_router_settings_path, "fallbacks DAG valid",
                   not rs_problems, "OK" if not rs_problems else "see fallbacks report")
        except RuntimeError as e:
            record(config.litellm_router_settings_path, "merged shape", False, str(e))

    return {"checks": checks, "errors": errors}
