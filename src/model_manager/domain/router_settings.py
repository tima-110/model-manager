"""Merge generated fallbacks and model_group_alias into a stub router_settings file.

LiteLLM ``include:`` merging is shallow, so separate ``fallbacks`` and
``model_group_alias`` snippets do not deep-merge reliably. This module starts
from a stub file (managed by hand, holding routing strategy, retries, and
manually curated aliases) and produces a single complete ``router_settings``
document. The stub is never overwritten; output always goes to a new file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from model_manager.config import AppConfig


def _load_yaml_doc(path: Path) -> dict:
    """Load a YAML file and return its mapping. Raises RuntimeError on problems."""
    if not path.exists():
        raise RuntimeError(f"YAML file not found: {path}")
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid YAML in {path}: {e}") from e
    except PermissionError as e:
        raise RuntimeError(f"Permission denied reading {path}: {e}") from e
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise RuntimeError(f"Expected a YAML mapping in {path}, got {type(doc).__name__}")
    return doc


def extract_fallbacks(doc: dict) -> list[dict[str, list[str]]]:
    """Return the fallbacks list from either ``{fallbacks: [...]}`` or wrapped shape."""
    if not isinstance(doc, dict):
        raise RuntimeError("Expected a YAML mapping when extracting fallbacks")
    raw: Any = None
    if isinstance(doc.get("fallbacks"), list):
        raw = doc["fallbacks"]
    elif isinstance(doc.get("router_settings"), dict) and isinstance(
        doc["router_settings"].get("fallbacks"), list
    ):
        raw = doc["router_settings"]["fallbacks"]
    else:
        raise RuntimeError(
            "No 'fallbacks' list found. Expected top-level 'fallbacks:' "
            "or 'router_settings: fallbacks:'."
        )
    return raw


def extract_alias_map(doc: dict) -> dict[str, Any]:
    """Return the alias map from either ``{model_group_alias: {...}}`` or wrapped shape."""
    if not isinstance(doc, dict):
        raise RuntimeError("Expected a YAML mapping when extracting model_group_alias")
    raw: Any = None
    if isinstance(doc.get("model_group_alias"), dict):
        raw = doc["model_group_alias"]
    elif isinstance(doc.get("router_settings"), dict) and isinstance(
        doc["router_settings"].get("model_group_alias"), dict
    ):
        raw = doc["router_settings"]["model_group_alias"]
    else:
        raise RuntimeError(
            "No 'model_group_alias' mapping found. Expected top-level "
            "'model_group_alias:' or 'router_settings: model_group_alias:'."
        )
    return dict(raw)


def _fallback_subject(item: dict) -> str | None:
    """Return the single subject key of a fallbacks entry, or None if unusable."""
    if not isinstance(item, dict) or len(item) != 1:
        return None
    return next(iter(item))


def merge_fallbacks(
    stub_list: list[dict[str, list[str]]],
    generated_list: list[dict[str, list[str]]],
) -> list[dict[str, list[str]]]:
    """Merge stub + generated fallbacks. Same subject -> generated wins.

    Stub entries that are malformed (not single-key dicts) are preserved
    verbatim at the front so hand-managed content is never silently dropped.
    """
    merged: list[dict[str, list[str]]] = []
    index: dict[str, int] = {}
    for item in stub_list:
        subject = _fallback_subject(item)
        if subject is None:
            merged.append(item)
            continue
        if subject in index:
            merged[index[subject]] = item
        else:
            index[subject] = len(merged)
            merged.append(item)
    for item in generated_list:
        subject = _fallback_subject(item)
        if subject is None:
            merged.append(item)
            continue
        if subject in index:
            merged[index[subject]] = item
        else:
            index[subject] = len(merged)
            merged.append(item)
    return merged


def merge_aliases(
    stub_map: dict[str, Any],
    generated_map: dict[str, Any],
) -> dict[str, Any]:
    """Merge alias maps. Generated keys (tier1/2/3) overwrite stub on collision."""
    return {**stub_map, **generated_map}


def build_merged_router_settings(
    config: AppConfig,
    *,
    limit: int = 5,
    from_files: bool = False,
    stub_path: Path | None = None,
) -> dict:
    """Build the merged ``{"router_settings": {...}}`` document.

    Default mode regenerates fallbacks/aliases in-memory from models.json.
    With ``from_files=True`` the already-generated fallbacks/aliases YAML
    files are read instead (both top-level and router_settings-wrapped
    shapes are accepted).
    """
    stub_file = stub_path or config.litellm_router_settings_stub_path
    stub_doc = _load_yaml_doc(stub_file)

    router_block = stub_doc.get("router_settings", {})
    if router_block is None:
        router_block = {}
    if not isinstance(router_block, dict):
        raise RuntimeError(
            f"'router_settings' in {stub_file} must be a mapping, "
            f"got {type(router_block).__name__}"
        )
    merged_block: dict[str, Any] = dict(router_block)

    if from_files:
        fallbacks_doc = _load_yaml_doc(config.litellm_fallbacks_path)
        aliases_doc = _load_yaml_doc(config.litellm_aliases_path)
        generated_fallbacks = extract_fallbacks(fallbacks_doc)
        generated_aliases = extract_alias_map(aliases_doc)
    else:
        from model_manager.domain import fallbacks as fallbacks_mod
        from model_manager.domain import model_group_aliases as aliases_mod

        generated_fallbacks = fallbacks_mod.build_fallbacks(config, limit=limit)
        generated_aliases = aliases_mod.build_alias_map(config)

    stub_fallbacks = merged_block.get("fallbacks", [])
    if stub_fallbacks is None:
        stub_fallbacks = []
    if not isinstance(stub_fallbacks, list):
        raise RuntimeError(
            f"'fallbacks' in {stub_file} must be a list, "
            f"got {type(stub_fallbacks).__name__}"
        )
    merged_block["fallbacks"] = merge_fallbacks(stub_fallbacks, generated_fallbacks)

    stub_aliases = merged_block.get("model_group_alias", {})
    if stub_aliases is None:
        stub_aliases = {}
    if not isinstance(stub_aliases, dict):
        raise RuntimeError(
            f"'model_group_alias' in {stub_file} must be a mapping, "
            f"got {type(stub_aliases).__name__}"
        )
    merged_block["model_group_alias"] = merge_aliases(dict(stub_aliases), dict(generated_aliases))

    return {"router_settings": merged_block}


def generate_router_settings_yaml(
    config: AppConfig,
    *,
    dry_run: bool = False,
    output_path: Path | None = None,
    limit: int = 5,
    from_files: bool = False,
    stub_path: Path | None = None,
) -> str | None:
    """Serialize the merged router_settings to YAML. Returns string on dry_run.

    Never writes to the stub file; refuses if output resolves to the stub path.
    """
    stub_file = stub_path or config.litellm_router_settings_stub_path
    out_path = output_path or config.litellm_router_settings_path
    if out_path.resolve() == stub_file.resolve():
        raise RuntimeError(
            f"Refusing to overwrite the stub file ({stub_file}). "
            "Choose a different --output path."
        )

    doc = build_merged_router_settings(
        config, limit=limit, from_files=from_files, stub_path=stub_file
    )
    yaml_doc = yaml.safe_dump(
        doc,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    if dry_run:
        return yaml_doc

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        out_path.rename(bak_path)

    out_path.write_text(yaml_doc)
    return None
