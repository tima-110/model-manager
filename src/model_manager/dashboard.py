"""Dashboard generation: collect data and render self-contained HTML."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import yaml

from model_manager.config import (
    AppConfig,
    get_free_models_path,
    get_nvidia_models_path,
    get_ollama_models_path,
    get_gemini_models_path,
    get_models_path,
    get_scores_path,
    get_raw_scores_path,
    get_litellm_cost_overrides_path,
    get_litellm_cost_map_output_path,
)
from model_manager.domain import storage, scores


def generate_dashboard(cfg: AppConfig) -> Path:
    """Gather data, render HTML, write to data_dir. Returns output path."""
    data = _collect_data(cfg)
    html_content = _render_html(data)
    output = cfg.data_dir / "dashboard.html"
    output.write_text(html_content)
    return output


def _collect_data(cfg: AppConfig) -> dict:
    """Assemble all dashboard data into a plain dict."""
    version = _get_version()
    models_data = storage.load_models_data(cfg)
    all_scores = scores.list_all_scores(cfg)

    lib_models = models_data.get("models", {})
    meta = models_data.get("meta", {})

    # --- Model counts ---
    total_models = len(lib_models)
    total_variants = 0
    total_mappings = 0
    scored_mappings = 0  # provider IDs where the variant has AA scores

    # --- Health data per provider mapping ---
    health_rows: list[dict] = []

    for mid, m_info in lib_models.items():
        display_name = m_info.get("display_name", mid)
        family = m_info.get("family", "unknown")
        variants = m_info.get("variants", {})
        total_variants += len(variants)

        for vid, v_info in variants.items():
            slug = v_info.get("aa_slug")
            variant_scores = v_info.get("scores", {})

            provider_ids = v_info.get("provider_ids", {})
            for prov, pids in provider_ids.items():
                if not isinstance(pids, dict):
                    continue
                for pid, scan_data in pids.items():
                    total_mappings += 1
                    has_scores = bool(variant_scores and (
                        variant_scores.get("intelligence") is not None or
                        variant_scores.get("coding") is not None or
                        variant_scores.get("math") is not None
                    ))
                    if has_scores:
                        scored_mappings += 1

                    assessment = scan_data.get("assessment", "Unknown")
                    availability = scan_data.get("availability")
                    latency = scan_data.get("avg_latency")
                    scan_ts = scan_data.get("scan_timestamp")

                    health_rows.append({
                        "model": mid,
                        "display_name": display_name,
                        "variant": vid,
                        "provider": prov,
                        "provider_id": pid,
                        "assessment": assessment,
                        "availability": availability,
                        "latency": latency,
                        "scan_timestamp": scan_ts,
                        "has_scores": has_scores,
                    })

    # --- Top scores by metric ---
    score_rankings = _build_score_rankings(lib_models, all_scores)

    # --- Provider snapshots ---
    providers = ["openrouter", "nvidia", "ollama", "gemini"]
    provider_paths = {
        "openrouter": (get_free_models_path(cfg), "OpenRouter"),
        "nvidia": (get_nvidia_models_path(cfg), "NVIDIA"),
        "ollama": (get_ollama_models_path(cfg), "Ollama"),
        "gemini": (get_gemini_models_path(cfg), "Gemini"),
    }
    provider_snapshots = {}
    for key, (path, label) in provider_paths.items():
        snapshot = _parse_provider_cache(path)
        snapshot["label"] = label
        snapshot["mapped"] = _count_mapped_for_provider(lib_models, label)
        provider_snapshots[key] = snapshot

    # --- Cost map ---
    # --- LiteLLM config ---
    litellm_config_path = cfg.litellm_config_path
    litellm_config_valid = False
    litellm_config_error = None
    if litellm_config_path.exists():
        try:
            yaml.safe_load(litellm_config_path.read_text())
            litellm_config_valid = True
        except yaml.YAMLError as e:
            litellm_config_error = str(e)
        except PermissionError:
            litellm_config_error = "Permission denied"
        except OSError as e:
            litellm_config_error = str(e)
    else:
        litellm_config_error = "File not found"

    cost_overrides_path = get_litellm_cost_overrides_path(cfg)
    cost_output_path = get_litellm_cost_map_output_path(cfg)
    cost_overrides_count = 0
    if cost_overrides_path.exists():
        try:
            cost_overrides_count = len(json.loads(cost_overrides_path.read_text()))
        except Exception:
            pass

    cost_map_upstream_count = 0
    if cost_output_path.exists():
        try:
            cost_map_upstream_count = len(json.loads(cost_output_path.read_text()))
        except Exception:
            pass

    # --- Model reference table ---
    model_rows: list[dict] = []
    for mid, m_info in lib_models.items():
        display_name = m_info.get("display_name", mid)
        family = m_info.get("family", "unknown")
        variants = m_info.get("variants", {})
        default_variant = m_info.get("default_variant", "standard")

        variant_names = sorted(variants.keys())
        providers = set()
        for vid, v_info in variants.items():
            for p in v_info.get("provider_ids", {}):
                providers.add(p)

        model_rows.append({
            "model_id": mid,
            "display_name": display_name,
            "family": family,
            "variants": ", ".join(variant_names),
            "default_variant": default_variant,
            "providers": ", ".join(sorted(providers)),
        })

    last_updated = meta.get("last_updated", None)

    return {
        "version": version,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_models": total_models,
        "total_variants": total_variants,
        "total_mappings": total_mappings,
        "scored_mappings": scored_mappings,
        "last_updated": last_updated,
        "health_rows": health_rows,
        "score_rankings": score_rankings,
        "model_rows": model_rows,
        "provider_snapshots": provider_snapshots,
        "provider_coverage": f"{sum(1 for s in provider_snapshots.values() if s['count'] > 0)}/{len(providers)}",
        "cost_overrides_count": cost_overrides_count,
        "cost_map_entries": cost_map_upstream_count,
        "cost_overrides_path": str(cost_overrides_path),
        "cost_map_output_path": str(cost_output_path),
        "litellm_config": {
            "path": str(litellm_config_path),
            "valid": litellm_config_valid,
            "error": litellm_config_error,
            "exists": litellm_config_path.exists(),
        },
        "config": {
            "data_dir": str(cfg.data_dir),
            "scan_frequency": cfg.scan_frequency,
            "litellm_service_dir": str(cfg.litellm_service_dir),
            "litellm_config_path": str(cfg.litellm_config_path),
            "litellm_cost_map_url": cfg.litellm_cost_map_url,
        },
        "paths": {
            "config_file": _find_config_path(cfg),
            "data_dir": str(cfg.data_dir),
            "models_json": str(get_models_path(cfg)),
            "scores_json": str(get_scores_path(cfg)),
            "raw_scores_json": str(get_raw_scores_path(cfg)),
            "provider_caches": [str(p[0]) for p in provider_paths.values()],
            "cost_map_output": str(cost_output_path),
        },
    }


def _get_version() -> str:
    try:
        from importlib.metadata import version as v
        return v("model-manager")
    except Exception:
        return "unknown"


def _find_config_path(cfg: AppConfig) -> str:
    from model_manager.config import find_config
    path = find_config()
    return str(path) if path else "Not found"


def _parse_provider_cache(path: Path) -> dict:
    """Read a provider cache file and return basic stats."""
    if not path.exists():
        return {"count": 0, "available": False}
    try:
        data = json.loads(path.read_text())
        models_list = data.get("models", [])
        fetched_at = data.get("fetched_at")
        return {
            "count": len(models_list),
            "available": True,
            "fetched_at": fetched_at,
        }
    except Exception:
        return {"count": 0, "available": False}


def _count_mapped_for_provider(lib_models: dict, provider_label: str) -> int:
    """Count how many model variants have a mapping for this provider."""
    count = 0
    for mid, m_info in lib_models.items():
        for vid, v_info in m_info.get("variants", {}).items():
            provider_ids = v_info.get("provider_ids", {})
            for prov in provider_ids:
                if prov.lower() == provider_label.lower():
                    count += 1
    return count


def _build_score_rankings(lib_models: dict, all_scores: dict) -> dict:
    """Build rankings for intelligence and coding."""
    metrics = ["intelligence", "coding"]
    rankings: dict = {}
    for metric in metrics:
        entries = []
        for mid, m_info in lib_models.items():
            for vid, v_info in m_info.get("variants", {}).items():
                slug = v_info.get("aa_slug")
                if not slug or slug not in all_scores:
                    continue
                s = all_scores[slug].get("scores", {})
                val = s.get(metric)
                if val is not None:
                    entries.append({
                        "model": mid,
                        "variant": vid,
                        "value": val,
                    })
        entries.sort(key=lambda x: x["value"], reverse=True)
        rankings[metric] = entries
    return rankings


def _render_html(data: dict) -> str:
    """Produce complete HTML string from collected data."""
    r = data  # shorthand
    ver = html.escape(r["version"])
    generated = html.escape(r["generated_at"])
    last_upd = html.escape(r["last_updated"] or "Never")

    # --- Summary cards ---
    cards = f"""
    <div class="cards">
      <div class="card"><div class="card-value">{r['total_models']}</div><div class="card-label">Models</div></div>
      <div class="card"><div class="card-value">{r['total_variants']}</div><div class="card-label">Variants</div></div>
      <div class="card"><div class="card-value">{r['total_mappings']}</div><div class="card-label">Provider Mappings</div></div>
      <div class="card"><div class="card-value">{r['scored_mappings']}</div><div class="card-label">Scored</div></div>
      <div class="card"><div class="card-value">{html.escape(r['provider_coverage'])}</div><div class="card-label">Providers Fetched</div></div>
    </div>"""

    # --- Score rankings ---
    score_sections = ""
    METRIC_LABELS = {"intelligence": "Intelligence", "coding": "Coding"}
    for metric, label in METRIC_LABELS.items():
        entries = r["score_rankings"].get(metric, [])
        score_sections += f'<div class="ranking-column"><h3>{html.escape(label)}</h3>'
        if entries:
            score_sections += '<table class="ranking-table"><tr><th>#</th><th>Model</th><th>Variant</th><th>Score</th></tr>'
            for i, e in enumerate(entries, 1):
                mn = html.escape(e["model"])
                vr = html.escape(e["variant"])
                sc = e["value"]
                score_sections += f"<tr><td>{i}</td><td>{mn}</td><td>{vr}</td><td class='score-cell'>{sc}</td></tr>"
            score_sections += "</table>"
        else:
            score_sections += '<div class="no-data">No score data</div>'
        score_sections += "</div>"

    # --- Health table ---
    health_rows = r["health_rows"]
    health_table = ""
    if health_rows:
        health_table = '<table class="data-table"><tr><th>Model</th><th>Variant</th><th>Provider</th><th>ID</th><th>Status</th><th>Avail</th><th>Latency</th><th>Scanned</th></tr>'
        for row in health_rows:
            status = html.escape(row["assessment"])
            color = _status_color(status)
            avail = f"{row['availability']:.0%}" if row["availability"] is not None else "N/A"
            lat = f"{row['latency']:.1f}ms" if row["latency"] is not None else "N/A"
            scanned = row.get("scan_timestamp")
            scanned_fmt = scanned[:10] if scanned else "N/A"
            health_table += (
                f"<tr>"
                f"<td>{html.escape(row['model'])}</td>"
                f"<td>{html.escape(row['variant'])}</td>"
                f"<td>{html.escape(row['provider'])}</td>"
                f"<td class='mono'>{html.escape(row['provider_id'])}</td>"
                f"<td class='status-dot' style='--status-color:{color}'>{status}</td>"
                f"<td>{avail}</td>"
                f"<td>{lat}</td>"
                f"<td>{scanned_fmt}</td>"
                f"</tr>"
            )
        health_table += "</table>"
    else:
        health_table = '<div class="no-data">No provider mappings found. Run <code>model-manager models discover</code> to add mappings.</div>'

    # --- Model reference table ---
    model_rows = r["model_rows"]
    model_table = ""
    if model_rows:
        model_table = '<table class="data-table"><tr><th>Model ID</th><th>Display Name</th><th>Variants</th><th>Providers</th></tr>'
        for row in model_rows:
            model_table += (
                f"<tr>"
                f"<td class='mono'>{html.escape(row['model_id'])}</td>"
                f"<td>{html.escape(row['display_name'])}</td>"
                f"<td>{html.escape(row['variants'])}</td>"
                f"<td>{html.escape(row['providers'])}</td>"
                f"</tr>"
            )
        model_table += "</table>"
    else:
        model_table = '<div class="no-data">No models defined. Add models with <code>model-manager models add</code>.</div>'

    # --- Provider snapshots ---
    prov_sections = ""
    for key, snap in r["provider_snapshots"].items():
        label = html.escape(snap["label"])
        count = snap["count"]
        mapped = snap["mapped"]
        available = snap["available"]
        fetched_at = snap.get("fetched_at")
        status_color = "#9ece6a" if available else "#565f89"
        if available and fetched_at:
            status_text = f"Fetched {html.escape(fetched_at[:10])}"
        elif available:
            status_text = "Fetched (date unknown)"
        else:
            status_text = "Not fetched"
        prov_sections += f"""
        <div class="card">
          <div class="provider-name">{label}</div>
          <div class="provider-stats">
            <span class="stat"><span class="stat-val">{count}</span> discovered</span>
            <span class="stat"><span class="stat-val">{mapped}</span> mapped</span>
          </div>
          <div class="provider-status" style="color:{status_color}">{status_text}</div>
        </div>"""

    # --- Cost map ---
    cost_section = f"""
    <table class="data-table">
      <tr><td>Cost map entries</td><td>{r['cost_map_entries']}</td></tr>
      <tr><td>Local overrides</td><td>{r['cost_overrides_count']}</td></tr>
      <tr><td>Output path</td><td class="mono">{html.escape(r['cost_map_output_path'])}</td></tr>
    </table>"""

    # --- LiteLLM config section ---
    lc = r["litellm_config"]
    lc_status = "Valid YAML" if lc["valid"] else html.escape(lc["error"] or "Unknown")
    lc_color = "#9ece6a" if lc["valid"] else ("#e0af68" if not lc["exists"] else "#f7768e")
    lc_section = f"""
    <table class="data-table">
      <tr><td>Config path</td><td class="mono">{html.escape(lc['path'])}</td></tr>
      <tr><td>Status</td><td><span style="color:{lc_color}">&#9679;</span> {lc_status}</td></tr>
    </table>"""

    # --- Config table ---
    cfg_table = ""
    for key, val in r["config"].items():
        cfg_table += f"<tr><td>{html.escape(key)}</td><td class='mono'>{html.escape(str(val))}</td></tr>"

    # --- Paths table ---
    paths_table = ""
    # Only show non-empty paths that exist
    path_entries = [
        ("Config file", r["paths"]["config_file"]),
        ("Data directory", r["paths"]["data_dir"]),
        ("Models JSON", r["paths"]["models_json"]),
        ("Scores JSON", r["paths"]["scores_json"]),
        ("Cost map output", r["paths"]["cost_map_output"]),
    ]
    for label_, p in path_entries:
        exists_mark = '<span style="color:#9ece6a">&#9679;</span>' if Path(p).exists() else ""
        paths_table += f"<tr><td>{html.escape(label_)}</td><td class='mono'>{exists_mark} {html.escape(p)}</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>model-manager Dashboard</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 1rem;
    background: #1a1b26; color: #a9b1d6;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    line-height: 1.6;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; color: #c0caf5; margin: 0 0 0.25rem; }}
  .subtitle {{ color: #565f89; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  h2 {{ font-size: 1.1rem; color: #c0caf5; border-bottom: 1px solid #24283b; padding-bottom: 0.4rem; margin: 1.5rem 0 0.75rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.75rem; }}
  .card {{
    background: #24283b; border-radius: 8px; padding: 1rem;
    display: flex; flex-direction: column; align-items: center; text-align: center;
  }}
  .card-value {{ font-size: 1.6rem; font-weight: 700; color: #7aa2f7; }}
  .card-label {{ font-size: 0.78rem; color: #565f89; text-transform: uppercase; letter-spacing: 0.04em; }}
  .provider-name {{ font-weight: 600; color: #c0caf5; margin-bottom: 0.25rem; }}
  .provider-stats {{ font-size: 0.85rem; color: #a9b1d6; }}
  .stat {{ margin: 0 0.3rem; }}
  .stat-val {{ font-weight: 600; color: #7aa2f7; }}
  .provider-status {{ font-size: 0.78rem; margin-top: 0.25rem; }}
  .rankings {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.75rem; }}
  .ranking-column h3 {{ font-size: 0.95rem; color: #c0caf5; margin: 0 0 0.4rem; }}
  .data-table, .ranking-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .data-table th, .ranking-table th {{ text-align: left; color: #565f89; font-weight: 600; padding: 0.4rem 0.5rem; border-bottom: 1px solid #24283b; }}
  .data-table td, .ranking-table td {{ padding: 0.35rem 0.5rem; border-bottom: 1px solid #1a1b26; }}
  .data-table tr:nth-child(even), .ranking-table tr:nth-child(even) {{ background: #1d1f2e; }}
  .data-table tr:hover {{ background: #2f3348; }}
  .mono {{ font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 0.8rem; }}
  .score-cell {{ font-weight: 600; color: #9ece6a; text-align: right; }}
  .status-dot {{ position: relative; padding-left: 1.2rem; }}
  .status-dot::before {{
    content: ''; position: absolute; left: 0.3rem; top: 50%; transform: translateY(-50%);
    width: 8px; height: 8px; border-radius: 50%; background: var(--status-color, #565f89);
  }}
  .no-data {{ color: #565f89; font-style: italic; padding: 0.5rem 0; font-size: 0.85rem; }}
  code {{ background: #1d1f2e; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.82rem; }}
  .section-row {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; }}
  @media (max-width: 600px) {{ .rankings {{ grid-template-columns: 1fr; }} .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>
<div class="container">
  <h1>model-manager Dashboard</h1>
  <div class="subtitle">v{ver} &mdash; Generated {generated} &mdash; Last model update: {last_upd}</div>

  <h2>Summary</h2>
  {cards}

  <h2>Top Scores</h2>
  <div class="rankings">{score_sections}</div>

  <h2>Provider Snapshots</h2>
  <div class="section-row">{prov_sections}</div>

  <h2>Model Health</h2>
  {health_table}

  <h2>Model Reference</h2>
  {model_table}

  <h2>LiteLLM Cost Map</h2>
  {cost_section}

  <h2>LiteLLM Config</h2>
  {lc_section}

  <h2>Configuration</h2>
  <table class="data-table">{cfg_table}</table>

  <h2>System Paths</h2>
  <table class="data-table">{paths_table}</table>
</div>
</body>
</html>"""


def _status_color(status: str) -> str:
    colors = {
        "Good": "#9ece6a",
        "Slow": "#e0af68",
        "Weak": "#e0af68",
        "Ratelimited": "#e0af68",
        "Unauthorized": "#bb9af7",
        "Not Found": "#f7768e",
        "Dead": "#f7768e",
    }
    return colors.get(status, "#565f89")
