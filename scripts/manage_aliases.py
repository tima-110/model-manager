#!/usr/bin/env python3
import json
import os
import sys
import argparse
import difflib
from datetime import datetime

# Configuration
DATA_DIR = "data"
ALIASES_PATH = os.path.join(DATA_DIR, "models.json")
SCORES_PATH = os.path.join(DATA_DIR, "model_scores.json")

def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def resolve_id(provider_id, aliases, scores):
    """
    Resolves a provider_id to its AA slug and retrieves scores.
    Resolution Chain: Explicit Match -> Default Variant Fallback -> (UI suggests fuzzy match)
    """
    # 1. Explicit Match
    for model_id, model_data in aliases.get("models", {}).items():
        for variant_id, variant_data in model_data.get("variants", {}).items():
            for provider, ids in variant_data.get("provider_ids", {}).items():
                if provider_id in ids:
                    aa_slug = variant_data.get("aa_slug")
                    score_data = scores.get("models", {}).get(aa_slug)
                    return {
                        "model": model_id,
                        "variant": variant_id,
                        "aa_slug": aa_slug,
                        "scores": score_data,
                        "method": "explicit"
                    }

    # 2. Model Key Match (Fallback to default variant)
    if provider_id in aliases.get("models", {}):
        model_data = aliases["models"][provider_id]
        default_variant = model_data.get("default_variant")
        if default_variant:
            variant_data = model_data["variants"].get(default_variant)
            if variant_data:
                aa_slug = variant_data.get("aa_slug")
                score_data = scores.get("models", {}).get(aa_slug)
                return {
                    "model": provider_id,
                    "variant": default_variant,
                    "aa_slug": aa_slug,
                    "scores": score_data,
                    "method": "default_fallback"
                }

    return None

def add_alias(provider, provider_id, model_id, variant_id, family=None, display_name=None):
    """Registers a provider_id to a model variant."""
    aliases = load_json(ALIASES_PATH)
    if "models" not in aliases:
        aliases["models"] = {}

    # Ensure model exists
    if model_id not in aliases["models"]:
        aliases["models"][model_id] = {
            "display_name": display_name or model_id.replace("-", " ").title(),
            "family": family or "unknown",
            "variants": {},
            "default_variant": "standard"
        }

    # Ensure variant exists
    model = aliases["models"][model_id]
    if variant_id not in model["variants"]:
        model["variants"][variant_id] = {
            "aa_slug": None, # To be filled or managed separately
            "provider_ids": {},
            "notes": ""
        }

    variant = model["variants"][variant_id]

    # Remove provider_id from any other existing mappings to ensure 1-to-1 mapping
    for mid, mdata in aliases["models"].items():
        for vid, vdata in mdata["variants"].items():
            for prov, ids in vdata["provider_ids"].items():
                if provider_id in ids:
                    ids.remove(provider_id)

    # Add to current variant
    if provider not in variant["provider_ids"]:
        variant["provider_ids"][provider] = []
    if provider_id not in variant["provider_ids"][provider]:
        variant["provider_ids"][provider].append(provider_id)

    # Update meta
    if "meta" not in aliases:
        aliases["meta"] = {}
    aliases["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    save_json(ALIASES_PATH, aliases)
    return True

def discover_aliases(provider, ids, scores):
    """Suggests AA slugs for unmapped provider IDs."""
    aliases = load_json(ALIASES_PATH)
    all_aa_slugs = list(scores.get("models", {}).keys())
    all_aa_names = [m.get("name") for m in scores.get("models", {}).values() if m.get("name")]

    suggestions = []
    for pid in ids:
        # Check if already mapped
        if resolve_id(pid, aliases, scores):
            continue

        # Clean ID for matching (remove provider prefix, free suffix, etc)
        clean_id = pid.split("/")[-1].split(":")[0].lower()

        # Find closest matches in slugs and names
        slug_matches = difflib.get_close_matches(clean_id, all_aa_slugs, n=3, cutoff=0.4)
        name_matches = difflib.get_close_matches(clean_id, all_aa_names, n=3, cutoff=0.4)

        # Prioritize slug matches
        candidates = slug_matches + [s for s in name_matches if s not in slug_matches]

        if candidates:
            suggestions.append({
                "pid": pid,
                "suggestions": candidates[:3]
            })

    return suggestions

def audit_mappings(known_ids):
    """Identifies which known IDs are missing mappings."""
    aliases = load_json(ALIASES_PATH)
    scores = load_json(SCORES_PATH)

    mapped = []
    missing = []

    for pid in known_ids:
        if resolve_id(pid, aliases, scores):
            mapped.append(pid)
        else:
            missing.append(pid)

    return {
        "total": len(known_ids),
        "mapped": len(mapped),
        "missing": len(missing),
        "missing_ids": missing
    }

def main():
    parser = argparse.ArgumentParser(description="Manage Model Aliases and Resolve Scores")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Resolve
    res_parser = subparsers.add_parser("resolve", help="Resolve a provider ID to scores")
    res_parser.add_argument("provider_id", help="The provider-specific model ID")

    # Add
    add_parser = subparsers.add_parser("add", help="Add a new mapping")
    add_parser.add_argument("--provider", required=True, help="Provider name (e.g. openrouter)")
    add_parser.add_argument("--id", required=True, help="Provider-specific ID")
    add_parser.add_argument("--model", required=True, help="Conceptual model ID")
    add_parser.add_argument("--variant", default="standard", help="Variant ID (default: standard)")
    add_parser.add_argument("--family", help="Model family (optional)")
    add_parser.add_argument("--display_name", help="Model display name (optional)")
    add_parser.add_argument("--aa_slug", help="The corresponding AA slug")

    # Discover
    disc_parser = subparsers.add_parser("discover", help="Suggest mappings for IDs")
    disc_parser.add_argument("--provider", required=True, help="Provider name")
    disc_parser.add_argument("--ids", required=True, help="Comma-separated list of IDs")

    # Audit
    audit_parser = subparsers.add_parser("audit", help="Audit mapping coverage")
    audit_parser.add_argument("--ids", required=True, help="Comma-separated list of IDs to audit")

    args = parser.parse_args()

    aliases = load_json(ALIASES_PATH)
    scores = load_json(SCORES_PATH)

    if args.command == "resolve":
        result = resolve_id(args.provider_id, aliases, scores)
        if result:
            print(f"Resolved: {args.provider_id} -> {result['model']} ({result['variant']}) -> {result['aa_slug']}")
            print(f"Scores: {result['scores']}")
        else:
            print(f"Error: No mapping found for {args.provider_id}")

    elif args.command == "add":
        # Load current aliases
        aliases = load_json(ALIASES_PATH)

        # Register the provider ID mapping
        add_alias(args.provider, args.id, args.model, args.variant, args.family, args.display_name)

        # Reload to get the updated structure from add_alias
        aliases = load_json(ALIASES_PATH)

        if args.aa_slug:
            if args.model in aliases.get("models", {}):
                model_variants = aliases["models"][args.model].get("variants", {})
                if args.variant in model_variants:
                    model_variants[args.variant]["aa_slug"] = args.aa_slug
                    save_json(ALIASES_PATH, aliases)

        print(f"Successfully mapped {args.id} to {args.model} ({args.variant})")

    elif args.command == "discover":
        ids = args.ids.split(",")
        suggestions = discover_aliases(args.provider, ids, scores)
        if not suggestions:
            print("All provided IDs are already mapped or no suggestions found.")
            return

        for sug in suggestions:
            print(f"\nUnmapped ID: {sug['pid']}")
            print(f"Suggested AA slugs: {', '.join(sug['suggestions'])}")
            choice = input("Accept first suggestion? [y/N]: ")
            if choice.lower() == 'y':
                slug = sug['suggestions'][0]
                # For discovery, we'll use the slug as the conceptual model ID for simplicity
                # and map it to the standard variant.
                add_alias(args.provider, sug['pid'], slug, "standard", family="unknown", display_name=slug)

                # Also update the aa_slug since it's a discovery match
                aliases = load_json(ALIASES_PATH)
                aliases["models"][slug]["variants"]["standard"]["aa_slug"] = slug
                save_json(ALIASES_PATH, aliases)
                print(f"Mapped {sug['pid']} to {slug} (standard)")

    elif args.command == "audit":
        ids = args.ids.split(",")
        report = audit_mappings(ids)
        print(f"\nMapping Audit Report")
        print(f"-------------------")
        print(f"Total Models: {report['total']}")
        print(f"Mapped:       {report['mapped']}")
        print(f"Missing:      {report['missing']}")
        print(f"\nMissing IDs:\n{', '.join(report['missing_ids'])}")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
