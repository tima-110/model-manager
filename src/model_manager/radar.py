"""Benchmark radar: static interactive HTML snapshot of model quality/cost/latency.

Unlike the Stitch tech spec (written as if backed by a live React API), this
page is fully static: all data is embedded as JSON at generation time from
local files (``models.json``, ``model_scores.json``, provider caches, scans,
optional ``model_prices.json``). No network, no polling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from model_manager.config import AppConfig, get_radar_output_path
from model_manager.domain import blocks, prices as prices_mod, scores, storage


PROXY_TIER = "IN_LITELLM_PROXY"
CANDIDATE_TIER = "PROVIDER_CANDIDATE"
REFERENCE_TIER = "AA_REFERENCE"
AA_INDEX_TIER = "AA_INDEX"

# Provider cache files that feed unmapped PROVIDER_CANDIDATE rows.
_PROVIDER_CACHES = (
    ("openrouter", "openrouter_free_models.json", "OpenRouter"),
    ("nvidia", "nvidia_available_models.json", "NVIDIA"),
    ("ollama", "ollama_available_models.json", "Ollama"),
    ("gemini", "gemini_available_models.json", "Gemini"),
    ("huggingface", "huggingface_available_models.json", "HuggingFace"),
)

_MAX_CANDIDATES = 200
_MAX_CATALOG = 1000


def generate_radar(cfg: AppConfig, out_path: Path | None = None) -> Path:
    """Collect data, render HTML, write to output path. Returns output path."""
    data = collect_radar_data(cfg)
    content = render_radar_html(data)
    output = get_radar_output_path(cfg, override=out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    return output


def _tag_tier(v_info: dict) -> str:
    for t in v_info.get("tags", []) or []:
        if str(t).startswith("tier-"):
            return str(t)
    return ""


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _variant_scores(v_info: dict, all_scores: dict) -> dict:
    """Prefer the variant snapshot; fall back to the AA score cache."""
    snap = v_info.get("scores") or {}
    if any(snap.get(k) is not None for k in ("intelligence", "coding", "agentic")):
        return {
            "intelligence": _num(snap.get("intelligence")),
            "coding": _num(snap.get("coding")),
            "agentic": _num(snap.get("agentic")),
            "ttft_s": _num(snap.get("ttft")),
            "tps": _num(snap.get("tps")),
        }
    slug = v_info.get("aa_slug")
    if slug and slug in all_scores:
        s = (all_scores[slug].get("scores") or {})
        return {
            "intelligence": _num(s.get("intelligence")),
            "coding": _num(s.get("coding")),
            "agentic": _num(s.get("agentic")),
            "ttft_s": _num(s.get("ttft")),
            "tps": _num(s.get("tps")),
        }
    return {"intelligence": None, "coding": None, "agentic": None, "ttft_s": None, "tps": None}


def _pid_lookup_keys(aa_slug: str | None, prov: str, pid: str, mid: str) -> list[str]:
    keys = []
    for k in (aa_slug, pid, f"{prov}/{pid}", mid):
        if k:
            keys.append(str(k))
    # OpenRouter ":free" suffix variant without suffix, for price joins.
    if pid.endswith(":free"):
        keys.append(pid[: -len(":free")])
    return keys


def collect_radar_data(cfg: AppConfig) -> dict:
    """Assemble the radar payload from local JSON files only."""
    models_data = storage.load_models_data(cfg)
    all_scores = scores.list_all_scores(cfg)
    price_lib = prices_mod.load_prices(cfg)
    try:
        blocked_keys = {e["key"] for e in blocks.blocked_summary(cfg)}
    except Exception:
        blocked_keys = set()

    lib_models = models_data.get("models", {})
    rows: list[dict] = []
    mapped_pids: set[str] = set()
    covered_slugs: set[str] = set()

    for mid, m_info in lib_models.items():
        display = m_info.get("display_name", mid)
        family = m_info.get("family", "unknown")
        variants = m_info.get("variants", {})
        for vid, v_info in variants.items():
            v_scores = _variant_scores(v_info, all_scores)
            included = v_info.get("include_in_litellm") is not False
            provider_ids = v_info.get("provider_ids", {}) or {}
            mappings = [
                (prov, pid, meta)
                for prov, pids in provider_ids.items()
                if not str(prov).startswith("include_") and isinstance(pids, dict)
                for pid, meta in pids.items()
                if not str(pid).startswith("include_")
            ]
            if not mappings:
                continue
            if v_info.get("aa_slug"):
                covered_slugs.add(str(v_info["aa_slug"]).lower())
            for prov, pid, meta in mappings:
                mapped_pids.add(str(pid).lower())
                mapped_pids.add(f"{prov}/{pid}".lower())
                meta = meta if isinstance(meta, dict) else {}
                lat = _num(meta.get("avg_latency"))
                ttft_ms = lat if lat else (
                    v_scores["ttft_s"] * 1000 if v_scores["ttft_s"] else None
                )
                price = prices_mod.lookup_price(
                    _pid_lookup_keys(v_info.get("aa_slug"), prov, pid, mid), price_lib
                )
                rows.append({
                    "id": f"{mid}/{vid}/{prov}/{pid}",
                    "model": mid,
                    "display": display,
                    "family": family,
                    "variant": vid,
                    "tier": PROXY_TIER,
                    "tag_tier": _tag_tier(v_info),
                    "provider": prov,
                    "provider_id": pid,
                    "arch": v_info.get("aa_slug") or "",
                    "scores": {
                        "intelligence": v_scores["intelligence"],
                        "coding": v_scores["coding"],
                        "agentic": v_scores["agentic"],
                        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
                        "tps": v_scores["tps"],
                    },
                    "health": {
                        "assessment": meta.get("assessment", "Unknown"),
                        "availability": meta.get("availability"),
                    },
                    "price_per_1m": price,
                    "litellm_included": included and (
                        (provider_ids.get(prov) or {}).get("include_in_litellm") is not False
                        if isinstance(provider_ids.get(prov), dict) else True
                    ),
                })

    # Add reference sets
    ref_sets = models_data.get("reference_sets", {})

    # Unmapped provider-cache models become candidates (score-less when unknown).
    slug_index = {s.lower(): s for s in all_scores}
    candidates = 0
    for prov_key, filename, label in _PROVIDER_CACHES:
        path = cfg.data_dir / filename
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text())
        except Exception:
            continue
        for m in doc.get("models", []) or []:
            if candidates >= _MAX_CANDIDATES:
                break
            pid = str(m.get("id", ""))
            if not pid or pid.lower() in mapped_pids:
                continue
            mapped_pids.add(pid.lower())
            slug = slug_index.get(pid.lower())
            if slug is None:
                short = pid.split("/")[-1].lower().replace(":free", "")
                slug = slug_index.get(short)
            s = (all_scores.get(slug, {}).get("scores") or {}) if slug else {}
            if slug:
                covered_slugs.add(slug.lower())
            price = prices_mod.lookup_price([slug or "", pid, f"{prov_key}/{pid}"], price_lib)
            ttft = _num(s.get("ttft"))
            rows.append({
                "id": f"candidate/{prov_key}/{pid}",
                "model": pid,
                "display": m.get("name") or pid,
                "family": label,
                "variant": "catalog",
                "tier": CANDIDATE_TIER,
                "tag_tier": "",
                "provider": prov_key,
                "provider_id": pid,
                "arch": "",
                "scores": {
                    "intelligence": _num(s.get("intelligence")),
                    "coding": _num(s.get("coding")),
                    "agentic": _num(s.get("agentic")),
                    "ttft_ms": round(ttft * 1000, 1) if ttft else None,
                    "tps": _num(s.get("tps")),
                },
                "health": {"assessment": "Unscanned", "availability": None},
                "price_per_1m": price,
                "litellm_included": False,
            })
            candidates += 1

    # Full AA catalog: every slug not already represented becomes a
    # table-only AA_INDEX row (excluded from the scatter unless pinned).
    pinned_slugs = {str(s).lower() for s in (cfg.radar.pinned_references or [])}
    for slug, entry in all_scores.items():
        if str(slug).lower() in covered_slugs or str(slug).lower() in pinned_slugs:
            continue
        s = (entry.get("scores") or {}) if isinstance(entry, dict) else {}
        ttft = _num(s.get("ttft"))
        rows.append({
            "id": f"aa/{slug}",
            "model": str(slug),
            "display": (entry.get("name") if isinstance(entry, dict) else None) or str(slug),
            "family": "AA",
            "variant": "catalog",
            "tier": AA_INDEX_TIER,
            "tag_tier": "",
            "provider": "aa_index",
            "provider_id": str(slug),
            "arch": "",
            "scores": {
                "intelligence": _num(s.get("intelligence")),
                "coding": _num(s.get("coding")),
                "agentic": _num(s.get("agentic")),
                "ttft_ms": round(ttft * 1000, 1) if ttft else None,
                "tps": _num(s.get("tps")),
            },
            "health": {"assessment": "Unmapped", "availability": None},
            "price_per_1m": prices_mod.lookup_price([str(slug)], price_lib),
            "litellm_included": False,
        })

    # Config-pinned AA baselines (empty by default).
    pins: list[dict] = []
    missing_pins: list[str] = []
    for slug in cfg.radar.pinned_references or []:
        entry = all_scores.get(slug) or all_scores.get(str(slug).lower())
        if entry is None:
            missing_pins.append(str(slug))
            continue
        s = entry.get("scores", {}) or {}
        ttft = _num(s.get("ttft"))
        pins.append({
            "id": f"reference/{slug}",
            "model": str(slug),
            "display": entry.get("name") or str(slug),
            "family": "AA",
            "variant": "baseline",
            "tier": REFERENCE_TIER,
            "tag_tier": "",
            "provider": "aa_index",
            "provider_id": str(slug),
            "arch": "",
            "scores": {
                "intelligence": _num(s.get("intelligence")),
                "coding": _num(s.get("coding")),
                "agentic": _num(s.get("agentic")),
                "ttft_ms": round(ttft * 1000, 1) if ttft else None,
                "tps": _num(s.get("tps")),
            },
            "health": {"assessment": "Reference", "availability": None},
            "price_per_1m": prices_mod.lookup_price([str(slug)], price_lib),
            "litellm_included": False,
        })
    rows.extend(pins)

    # Flagship: best intelligence among included proxy rows.
    flagship = None
    for r in rows:
        if r["tier"] != PROXY_TIER or not r["litellm_included"]:
            continue
        intel = r["scores"]["intelligence"]
        if intel is None:
            continue
        if flagship is None or intel > flagship["scores"]["intelligence"]:
            flagship = r

    recommendations = _build_recommendations(rows, flagship, blocked_keys)

    catalog = []
    for slug, info in sorted(all_scores.items(), key=lambda kv: str(kv[1].get("name") or kv[0]).lower()):
        if len(catalog) >= _MAX_CATALOG:
            break
        cs = (info.get("scores") or {})
        cttft = _num(cs.get("ttft"))
        catalog.append({
            "slug": slug,
            "name": info.get("name") or slug,
            "scores": {
                "intelligence": _num(cs.get("intelligence")),
                "coding": _num(cs.get("coding")),
                "agentic": _num(cs.get("agentic")),
                "ttft_ms": round(cttft * 1000, 1) if cttft else None,
                "tps": _num(cs.get("tps")),
            },
            "price_per_1m": prices_mod.lookup_price([slug], price_lib),
        })

    priced = sum(1 for r in rows if r["price_per_1m"] is not None)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "version": _get_version(),
        "flagship_id": flagship["id"] if flagship else None,
        "models": rows,
        "pins": [p["id"] for p in pins],
        "missing_pins": missing_pins,
        "recommendations": recommendations,
        "reference_catalog": catalog,
        "reference_sets": ref_sets,
        "counts": {
            "proxy": sum(1 for r in rows if r["tier"] == PROXY_TIER),
            "candidates": sum(1 for r in rows if r["tier"] == CANDIDATE_TIER),
            "references": sum(1 for r in rows if r["tier"] == REFERENCE_TIER),
            "aa_index": sum(1 for r in rows if r["tier"] == AA_INDEX_TIER),
            "priced": priced,
            "total": len(rows),
        },
    }


def _build_recommendations(rows: list[dict], flagship: dict | None, blocked: set[str]) -> list[dict]:
    """Derive up to 2 insight cards from local data (no dollar claims w/o prices)."""
    if flagship is None:
        return []
    f_scores = flagship["scores"]
    f_intel = f_scores["intelligence"]
    f_coding = f_scores["coding"]
    f_ttft = f_scores["ttft_ms"]
    f_name = flagship["display"]
    recs: list[dict] = []

    def _blocked(r: dict) -> bool:
        keys = {f"litellm:{r['display']}", f"alias:{r['display']}",
                f"provider:{r['provider']}/{r['provider_id']}"}
        return bool(keys & blocked)

    # Intelligence delta: best-scoring candidate above the flagship.
    best_q = None
    for r in rows:
        if r["tier"] != CANDIDATE_TIER or _blocked(r):
            continue
        intel = r["scores"]["intelligence"]
        if intel is None or f_intel is None or intel <= f_intel:
            continue
        if best_q is None or intel > best_q["scores"]["intelligence"]:
            best_q = r
    if best_q is not None:
        delta = best_q["scores"]["intelligence"] - f_intel
        price_note = ""
        if best_q["price_per_1m"] is not None and flagship["price_per_1m"]:
            pct = (best_q["price_per_1m"] - flagship["price_per_1m"]) / flagship["price_per_1m"] * 100
            price_note = f" Priced at ${best_q['price_per_1m']:.2f}/1M ({pct:+.0f}% vs flagship)."
        recs.append({
            "type": "QUALITY_DELTA",
            "tag": f"+{delta:.1f} Intelligence",
            "title": f"{best_q['display']} scores above {f_name}",
            "summary": (f"AA intelligence {best_q['scores']['intelligence']:.1f} vs "
                        f"{f_intel:.1f} flagship.{price_note}"),
            "target": best_q["id"],
        })

    # Latency parity: near-flagship coding at lower TTFT.
    best_l = None
    for r in rows:
        if r["tier"] != CANDIDATE_TIER or _blocked(r):
            continue
        c, t = r["scores"]["coding"], r["scores"]["ttft_ms"]
        if c is None or t is None or f_ttft is None:
            continue
        baseline = f_coding if f_coding is not None else f_intel
        if baseline is None or c < baseline - 3:
            continue
        if t >= f_ttft:
            continue
        if best_l is None or t < best_l["scores"]["ttft_ms"]:
            best_l = r
    if best_l is not None:
        recs.append({
            "type": "LATENCY",
            "tag": f"{best_l['scores']['ttft_ms']:.0f}ms TTFT",
            "title": f"{best_l['display']} near-parity coding at lower latency",
            "summary": (f"Coding {best_l['scores']['coding']:.1f} with TTFT "
                        f"{best_l['scores']['ttft_ms']:.0f}ms vs {f_ttft:.0f}ms flagship."),
            "target": best_l["id"],
        })
    return recs[:2]


def _get_version() -> str:
    try:
        from importlib.metadata import version as v
        return v("model-manager")
    except Exception:
        return "unknown"


def render_radar_html(data: dict) -> str:
    """Render the single-file radar page (inline CSS + vanilla JS)."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    template = _HTML_TEMPLATE
    return template.replace("__RADAR_PAYLOAD__", payload)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark Radar</title>
<style>
:root{
  --base:#0f131d; --card:#171b26; --card2:#1c1f2a; --border:#232734;
  --primary:#6366f1; --emerald:#10b981; --cyan:#06b6d4;
  --text:#f1f5f9; --muted:#94a3b8;
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;padding:1.5rem 1rem;background:var(--base);color:var(--text);
  font-family:Inter,-apple-system,'Segoe UI',system-ui,sans-serif;line-height:1.5}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:1.4rem;margin:0}
.sub{color:var(--muted);font-size:.82rem;margin:.25rem 0 1rem}
.pills{display:flex;flex-wrap:wrap;gap:.5rem;margin:.75rem 0}
.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:999px;
  font-size:.75rem;background:var(--card);border:1px solid var(--border)}
.dot{width:8px;height:8px;border-radius:50%}
.controls{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;background:var(--card);
  border:1px solid var(--border);border-radius:10px;padding:.7rem .9rem;margin-bottom:.9rem}
.controls input[type=text]{flex:1;min-width:220px;background:var(--base);color:var(--text);
  border:1px solid var(--border);border-radius:6px;padding:.45rem .6rem}
.controls select{background:var(--base);color:var(--text);border:1px solid var(--border);
  border-radius:6px;padding:.45rem .6rem}
.btn{background:var(--primary);color:#fff;border:0;border-radius:6px;padding:.45rem .8rem;cursor:pointer}
.btn.ghost{background:var(--card2);color:var(--text);border:1px solid var(--border)}
.btn.active{outline:2px solid var(--cyan)}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:.9rem}
@media(max-width:1000px){.grid2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.9rem}
.card h2{font-size:1rem;margin:0 0 .4rem}
.card h3{font-size:.9rem;margin:.6rem 0 .3rem}
.muted{color:var(--muted);font-size:.82rem}
#chart{position:relative;width:100%;height:480px;background:#0a0e18;border-radius:8px;overflow:hidden}
#chart svg{position:absolute;inset:0;width:100%;height:100%}
#hud{position:absolute;max-width:300px;background:#262a35f2;border:1px solid var(--border);
  border-radius:8px;padding:.6rem .7rem;font-size:.8rem;display:none;z-index:5}
#hud .row{display:flex;gap:.8rem;margin:.3rem 0;font-family:'JetBrains Mono',monospace;font-size:.78rem}
.tabs{display:flex;gap:.35rem;flex-wrap:wrap;margin:.5rem 0}
.tabs button{background:var(--card2);color:var(--muted);border:1px solid var(--border);
  border-radius:6px;padding:.3rem .6rem;cursor:pointer;font-size:.8rem}
.tabs button.active{color:var(--text);border-color:var(--primary)}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;color:var(--muted);font-weight:600;padding:.4rem .5rem;border-bottom:1px solid var(--border)}
td{padding:.35rem .5rem;border-bottom:1px solid #1a1d29}
tbody tr:hover{background:#20242f}
.mono{font-family:'JetBrains Mono',monospace;font-size:.76rem}
.num{text-align:right;font-family:'JetBrains Mono',monospace}
.badge{display:inline-block;padding:.05rem .5rem;border-radius:10px;font-size:.7rem;font-weight:700}
.b-proxy{background:#6366f133;color:#a5b4fc}.b-cand{background:#10b98122;color:#6ee7b7}
.b-ref{background:#06b6d422;color:#67e8f9}.b-aa{background:#94a3b822;color:#cbd5e1}
.chip{display:inline-flex;align-items:center;gap:.35rem;background:var(--card2);
  border:1px solid var(--border);border-radius:14px;padding:.15rem .3rem .15rem .6rem;font-size:.76rem;margin:.15rem}
.chip button{background:none;border:0;color:var(--muted);cursor:pointer;font-size:.85rem}
#modal{display:none;position:fixed;inset:0;background:#000a;z-index:20;align-items:center;justify-content:center}
#modal .box{background:var(--card);border:1px solid var(--border);border-radius:10px;
  max-width:640px;width:92%;padding:1rem;max-height:80vh;overflow:auto}
#modal pre{background:#0a0e18;padding:.7rem;border-radius:6px;overflow:auto;font-size:.78rem}
.legend{display:flex;flex-wrap:wrap;gap:.9rem;font-size:.78rem;color:var(--muted);margin-top:.4rem}
</style>
</head>
<body>
<div class="wrap">
  <h1>Benchmark Radar</h1>
  <div class="sub" id="meta"></div>
  <div class="pills" id="counts"></div>
  <div class="controls">
    <input type="text" id="q" placeholder="Filter models, providers, architectures...">
    <select id="dim">
      <option value="intelligence">Intelligence</option>
      <option value="coding">Coding</option>
      <option value="agentic">Agentic</option>
    </select>
    <button class="btn ghost active" id="scaleLog">Log-10</button>
    <button class="btn ghost" id="scaleLin">Linear</button>
    <button class="btn" id="exportBtn">Export YAML Diff</button>
  </div>
  <div class="grid2">
    <div class="card">
      <h2>Trade-Off Frontier: Intelligence vs. Cost</h2>
      <div class="muted">Price-less models sit on the no-price rail (right edge). Pareto curve needs &ge;2 priced points.</div>
      <div id="chart"><svg id="plot" viewBox="0 0 800 480" preserveAspectRatio="none"></svg><div id="hud"></div></div>
      <div class="legend">
        <span><span class="dot" style="display:inline-block;background:#818cf8"></span> In LiteLLM Proxy</span>
        <span><span class="dot" style="display:inline-block;background:#10b981"></span> Provider Candidate</span>
        <span><span class="dot" style="display:inline-block;background:#06b6d4;border-radius:0;transform:rotate(45deg)"></span> AA Reference</span>
        <span><span style="display:inline-block;width:16px;border-top:2px dashed #4cd7f6"></span> Pareto Frontier</span>
        <span><span style="display:inline-block;width:16px;border-top:2px dashed #06b6d4"></span> Pinned reference level</span>
      </div>
    </div>
    <div>
      <div class="card">
        <h2>Pin AA Reference</h2>
        <div class="muted">Seeded from <span class="mono">radar.pinned_references</span>; your pins persist in this browser only.</div>
        <div style="display:flex;gap:.4rem;margin-top:.5rem">
          <select id="pinSelect" style="flex:1;background:var(--base);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:.45rem"></select>
          <button class="btn" id="pinBtn">Pin</button>
        </div>
        <div id="chips" style="margin-top:.5rem"></div>
      </div>
      <div class="card" style="margin-top:.9rem">
        <h2>Route Optimization Insights</h2>
        <div id="insights"></div>
      </div>
    </div>
  </div>
  <div class="card" style="margin-top:.9rem">
    <h2>Model Evaluation Matrix</h2>
    <div class="tabs" id="tabs">
      <button data-tab="active" class="active">Proxy + Candidates</button>
      <button data-tab="IN_LITELLM_PROXY">In Proxy</button>
      <button data-tab="PROVIDER_CANDIDATE">Candidates</button>
      <button data-tab="AA_REFERENCE">Baselines</button>
      <button data-tab="AA_INDEX">AA Index</button>
      <button data-tab="all">All</button>
    </div>
    <div style="overflow-x:auto"><table id="matrix">
      <thead><tr>
        <th data-sort="model" style="cursor:pointer">Model <span class="sort-icon"></span></th>
        <th data-sort="status" style="cursor:pointer">Status <span class="sort-icon"></span></th>
        <th class="num" data-sort="intelligence" data-dim="intelligence" style="cursor:pointer">Intelligence <span class="sort-icon"></span></th>
        <th class="num" data-sort="coding" data-dim="coding" style="cursor:pointer">Coding <span class="sort-icon"></span></th>
        <th class="num" data-sort="agentic" data-dim="agentic" style="cursor:pointer">Agentic <span class="sort-icon"></span></th>
        <th class="num">TTFT</th>
        <th class="num">Price /1M</th><th>Action</th>
      </tr></thead>
      <tbody></tbody>
    </table></div>
  </div>
</div>
<div id="modal"><div class="box">
  <h2 style="margin-top:0">LiteLLM YAML Diff (draft)</h2>
  <div class="muted">Generated client-side from staged rows. Review before appending to your LiteLLM config.</div>
  <pre id="yamlOut"></pre>
  <div style="display:flex;gap:.5rem;justify-content:flex-end">
    <button class="btn ghost" id="copyBtn">Copy</button>
    <button class="btn ghost" id="closeBtn">Close</button>
  </div>
</div></div>
<script>
var DATA = __RADAR_PAYLOAD__;
var state = {q:"", dim:"intelligence", log:true, tab:"active", staged:[], pins:(DATA.pins||[]).slice(), sortCol:null, sortDir:"asc"};
try {
  var saved = JSON.parse(localStorage.getItem("radar_pins") || "[]");
  if (saved.length) state.pins = saved;
} catch(e) {}
var byId = {};
DATA.models.forEach(function(m){ byId[m.id] = m; });
var catalogBySlug = {};
DATA.reference_catalog.forEach(function(e){ catalogBySlug[e.slug] = e; });
// Remap a pin ID to the live row for the same model when one exists.
// Stale pins (e.g. reference/<slug> saved before the slug gained a
// library row) would otherwise render a duplicate alongside aa/<slug>.
function canonicalPinId(id){
  if (!id) return id;
  if (byId[id]) return id;
  var slug = id.indexOf("reference/") === 0 ? id.slice(10) : id;
  var hit = DATA.models.filter(function(m){ return m.provider_id === slug || m.model === slug || m.arch === slug; })[0];
  return hit ? hit.id : id;
}
// Normalize pins on load so stale IDs collapse onto live rows.
state.pins = state.pins.map(canonicalPinId);
state.pins = state.pins.filter(function(id, i){ return state.pins.indexOf(id) === i; });
try { localStorage.setItem("radar_pins", JSON.stringify(state.pins)); } catch(e) {}
// Materialize pinned catalog entries that have no library row so they
// render on the chart/table like config-seeded baselines.
function synthRowForPin(id){
  if (byId[id]) return byId[id];
  if (id.indexOf("reference/") !== 0) return null;
  var slug = id.slice(10);
  var e = catalogBySlug[slug];
  if (!e) return null;
  var row = {id:id, model:slug, display:e.name || slug, family:"AA", variant:"baseline",
    tier:"AA_REFERENCE", tag_tier:"", provider:"aa_index", provider_id:slug, arch:"",
    scores:e.scores, health:{assessment:"Reference", availability:null},
    price_per_1m:(e.price_per_1m === undefined ? null : e.price_per_1m),
    litellm_included:false};
  byId[id] = row;
  return row;
}
function allRows(){
  var rows = DATA.models.slice();
  var seen = {};
  rows.forEach(function(m){ seen[m.id] = true; });
  state.pins.forEach(function(id){
    if (seen[id]) return;
    var r = synthRowForPin(id);
    if (r) { rows.push(r); seen[id] = true; }
  });
  return rows;
}

function dimVal(m){
  return m.scores[state.dim];
}
function dimLabel(v){
  if (v === null || v === undefined) return "\\u2014";
  return (typeof v === "number") ? (Math.round(v*10)/10) : v;
}
function fmtPrice(p){ return (p === null || p === undefined) ? "\\u2014" : "$" + p.toFixed(2); }
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

function filtered(){
  var q = state.q.toLowerCase();
  var pinned = {};
  state.pins.forEach(function(id){ pinned[id] = true; });
  return allRows().filter(function(m){
    var tier = m.tier;
    if (pinned[m.id]) tier = "AA_REFERENCE";
    if (state.tab === "active") {
      // Working set: routed models, candidates, and anything you pinned.
      if (tier !== "IN_LITELLM_PROXY" && tier !== "PROVIDER_CANDIDATE" && !pinned[m.id]) return false;
    } else if (state.tab !== "all" && tier !== state.tab) return false;
    // Pins are filter-proof: they stay dotted, leveled, and listed while set.
    if (pinned[m.id]) return true;
    if (!q) return true;
    return (m.display + " " + m.provider + " " + m.provider_id + " " + (m.arch||"")).toLowerCase().indexOf(q) >= 0;
  });
}
function plotPoints(){
  // Points with a score; price optional (null -> no-price rail).
  // AA_INDEX rows are table-only unless pinned as a baseline.
  return filtered().map(function(m){ return {m:m, y:dimVal(m), c:m.price_per_1m}; })
    .filter(function(p){
      if (p.y === null || p.y === undefined) return false;
      if (p.m.tier === "AA_INDEX" && state.pins.indexOf(p.m.id) < 0) return false;
      return true;
    });
}
function xPix(c, cmin, cmax, W, pad){
  if (c === null || c === undefined) return W - pad.r - 14; // no-price rail
  var cc = Math.max(0.10, c);
  if (state.log) {
    var l0 = Math.log10(Math.max(0.10, cmin)), l1 = Math.log10(Math.max(0.11, cmax));
    if (l1 <= l0) return pad.l + (W - pad.l - pad.r) / 2;
    return pad.l + (Math.log10(cc) - l0) / (l1 - l0) * (W - pad.l - pad.r);
  }
  if (cmax <= cmin) return pad.l + (W - pad.l - pad.r) / 2;
  return pad.l + (cc - cmin) / (cmax - cmin) * (W - pad.l - pad.r);
}
function yPix(v, vmin, vmax, H, pad){
  if (vmax <= vmin) return H - pad.b - (H - pad.t - pad.b) / 2;
  return H - pad.b - (v - vmin) / (vmax - vmin) * (H - pad.t - pad.b);
}
function tierColor(tier, pinned){
  if (pinned || tier === "AA_REFERENCE") return "#06b6d4";
  if (tier === "PROVIDER_CANDIDATE") return "#10b981";
  return "#818cf8";
}
function renderMeta(){
  document.getElementById("meta").textContent =
    "v" + DATA.version + " \\u2014 Snapshot " + DATA.generated_at + " \\u2014 static file, no live connection" +
    (DATA.missing_pins && DATA.missing_pins.length ? " \\u2014 unknown pins: " + DATA.missing_pins.join(", ") : "");
  var c = DATA.counts;
  document.getElementById("counts").innerHTML =
    '<span class="pill"><span class="dot" style="background:#818cf8"></span>In Proxy: ' + c.proxy + '</span>' +
    '<span class="pill"><span class="dot" style="background:#10b981"></span>Candidates: ' + c.candidates + '</span>' +
    '<span class="pill"><span class="dot" style="background:#06b6d4"></span>Baselines: ' + c.references + '</span>' +
    '<span class="pill"><span class="dot" style="background:#94a3b8"></span>AA Index: ' + c.aa_index + '</span>' +
    '<span class="pill">Priced: ' + c.priced + '/' + c.total + ' (run <span class="mono">prices fetch</span> to fill gaps)</span>';
}
function renderScatter(){
  var svg = document.getElementById("plot");
  var W = 800, H = 480, pad = {l:64, r:44, t:24, b:52};
  var pts = plotPoints();
  var priced = pts.filter(function(p){ return p.c !== null && p.c !== undefined; });
  var costs = priced.map(function(p){ return Math.max(0.10, p.c); });
  var cmin = costs.length ? Math.min.apply(null, costs) : 0.10;
  var cmax = costs.length ? Math.max.apply(null, costs) : 20.0;
  if (cmin >= cmax) cmax = cmin * 2;
  var ys = pts.map(function(p){ return p.y; });
  var vmin = ys.length ? Math.min.apply(null, ys) : 0;
  var vmax = ys.length ? Math.max.apply(null, ys) : 100;
  if (vmax <= vmin) vmax = vmin + 1;
  var AX = "#908fa0"; // Stitch tick-label token
  // Nice-number ticks (1/2/5 steps) for an arbitrary domain.
  function niceNum(x){
    var exp = Math.floor(Math.log10(x));
    var f = x / Math.pow(10, exp);
    var nf = f < 1.5 ? 1 : (f < 3.5 ? 2 : (f < 7.5 ? 5 : 10));
    return nf * Math.pow(10, exp);
  }
  function niceTicks(lo, hi, n){
    if (!(hi > lo)) return [lo];
    var step = niceNum((hi - lo) / (n - 1));
    var ticks = [], t = Math.ceil(lo / step) * step;
    while (t <= hi + step * 1e-9) { ticks.push(+t.toFixed(10)); t += step; }
    return ticks.length ? ticks : [lo, hi];
  }
  function fmtMoney(v){
    if (v >= 100) return "$" + Math.round(v);
    if (v >= 1) return "$" + (Math.round(v * 100) / 100);
    return "$" + (Math.round(v * 100) / 100).toFixed(2);
  }
  function fmtY(v){
    return String(parseFloat(v.toFixed(1)));
  }
  var s = "";
  // Y axis: baseline + ticks + gridlines + title.
  var yticks = niceTicks(vmin, vmax, 6);
  yticks.forEach(function(t){
    var gy = yPix(t, vmin, vmax, H, pad);
    s += '<line x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + gy.toFixed(1) + '" y2="' + gy.toFixed(1) + '" stroke="#232734" stroke-dasharray="2 4"/>';
    s += '<line x1="' + (pad.l - 5) + '" x2="' + pad.l + '" y1="' + gy.toFixed(1) + '" y2="' + gy.toFixed(1) + '" stroke="' + AX + '"/>';
    s += '<text x="' + (pad.l - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" fill="' + AX + '" font-size="10" font-family="JetBrains Mono,monospace" text-anchor="end">' + fmtY(t) + '</text>';
  });
  s += '<line x1="' + pad.l + '" x2="' + pad.l + '" y1="' + pad.t + '" y2="' + (H - pad.b) + '" stroke="' + AX + '"/>';
  var yTitle = state.dim === "coding" ? "Coding" : (state.dim === "agentic" ? "Agentic" : "Intelligence");
  s += '<text x="14" y="' + (H / 2) + '" fill="' + AX + '" font-size="11" text-anchor="middle" transform="rotate(-90 14 ' + (H / 2) + ')">' + yTitle + '</text>';
  // X axis: baseline + ticks + gridlines + title.
  var xticks = [];
  if (priced.length) {
    if (state.log) {
      xticks = niceTicks(Math.log10(Math.max(0.10, cmin)), Math.log10(cmax), 5)
        .map(function(t){ return Math.pow(10, t); });
    } else {
      xticks = niceTicks(Math.max(0.10, cmin), cmax, 5);
    }
  }
  xticks.forEach(function(t){
    var gx = xPix(t, cmin, cmax, W, pad);
    s += '<line x1="' + gx.toFixed(1) + '" x2="' + gx.toFixed(1) + '" y1="' + pad.t + '" y2="' + (H - pad.b) + '" stroke="#232734" stroke-dasharray="2 4"/>';
    s += '<line x1="' + gx.toFixed(1) + '" x2="' + gx.toFixed(1) + '" y1="' + (H - pad.b) + '" y2="' + (H - pad.b + 5) + '" stroke="' + AX + '"/>';
    s += '<text x="' + gx.toFixed(1) + '" y="' + (H - pad.b + 18) + '" fill="' + AX + '" font-size="10" font-family="JetBrains Mono,monospace" text-anchor="middle">' + fmtMoney(t) + '</text>';
  });
  s += '<line x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + (H - pad.b) + '" y2="' + (H - pad.b) + '" stroke="' + AX + '"/>';
  var xTitle = priced.length
    ? "Blended Price ($/1M tokens, " + (state.log ? "log-10" : "linear") + ")"
    : "No price data \\u2014 run prices fetch";
  s += '<text x="' + ((pad.l + W - pad.r) / 2) + '" y="' + (H - 8) + '" fill="' + AX + '" font-size="11" text-anchor="middle">' + xTitle + '</text>';
  // Pareto over priced points (higher y wins left-to-right).
  var sorted = priced.slice().sort(function(a,b){ return a.c - b.c; });
  var frontier = [], best = -Infinity;
  sorted.forEach(function(p){ if (p.y > best) { best = p.y; frontier.push(p); } });
  if (frontier.length >= 2) {
    var d = frontier.map(function(p, i){
      return (i ? "L" : "M") + xPix(p.c, cmin, cmax, W, pad).toFixed(1) + " " + yPix(p.y, vmin, vmax, H, pad).toFixed(1);
    }).join(" ");
    s += '<path d="' + d + '" fill="none" stroke="#4cd7f6" stroke-width="1.75" stroke-dasharray="4 4" opacity="0.8"/>';
  }
  // Horizontal reference levels for pinned models (index scale, not price).
  // One dashed line per pinned row at its current-dimension value; labels
  // de-collided vertically (lines stay exact, only labels nudge).
  var pinLevels = [];
  allRows().forEach(function(m){
    if (state.pins.indexOf(m.id) < 0) return;
    var v = dimVal(m);
    if (v === null || v === undefined) return;
    pinLevels.push({m:m, v:v, y:yPix(v, vmin, vmax, H, pad), isRef: false});
  });

  // Reference Sets pinning
  var refSetColors = ["#f97316", "#eab308", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899"];
  var colorIndex = 0;
  if (state.pins) {
      state.pins.forEach(function(pinId) {
          if (pinId.indexOf("refset/") === 0) {
              var setId = pinId.slice(7);
              var refSet = DATA.reference_sets[setId];
              if (!refSet) return;
              var c = refSetColors[colorIndex % refSetColors.length];
              colorIndex++;
              var slugs = refSet.items.map(function(item) { return item.aa_slug; });

              // To properly reference all benchmark models (even unconfigured ones), iterate over reference_catalog
              DATA.reference_catalog.forEach(function(e) {
                 if (slugs.indexOf(e.slug) >= 0) {
                     var synthId = "reference/" + e.slug;
                     var m = byId[synthId] || synthRowForPin(synthId) || {id: synthId, display: e.name || e.slug, scores: e.scores};
                     var v = dimVal(m);
                     if (v === null || v === undefined) return;
                     // Prevent duplicates if already drawing this
                     if (pinLevels.filter(function(p) { return p.m.id === m.id && p.isRef; }).length > 0) return;
                     pinLevels.push({m:m, v:v, y:yPix(v, vmin, vmax, H, pad), isRef: true, c: c});
                 }
              });
          }
      });
  }

  pinLevels.sort(function(a,b){ return a.y - b.y; });
  var lastLy = -1e9;
  pinLevels.forEach(function(p){
    var ly = p.y;
    if (ly - lastLy < 14) ly = lastLy + 14;
    lastLy = ly;
    p.ly = ly;
  });
  pinLevels.forEach(function(p){
    var c = p.isRef ? p.c : "#06b6d4";
    s += '<line x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + p.y.toFixed(1) + '" y2="' + p.y.toFixed(1) + '" stroke="' + c + '" stroke-width="1" stroke-dasharray="6 3" opacity="0.55"/>';
    s += '<text x="' + (W - pad.r - 4) + '" y="' + (p.ly - 4).toFixed(1) + '" fill="' + c + '" font-size="10" font-family="JetBrains Mono,monospace" text-anchor="end" opacity="0.9">' + esc(p.m.display) + ' ' + dimLabel(p.v) + '</text>';
  });
  // Rail separator when unpriced points exist.
  if (priced.length !== pts.length) {
    var rx = W - pad.r - 28;
    s += '<line x1="' + rx + '" x2="' + rx + '" y1="' + pad.t + '" y2="' + (H - pad.b) + '" stroke="#565f89" stroke-dasharray="4 4"/>';
    s += '<text x="' + (W - pad.r - 14) + '" y="' + (H - 8) + '" fill="#565f89" font-size="10" text-anchor="middle">no price</text>';
  }
  pts.forEach(function(p, i){
    var cx = xPix(p.c, cmin, cmax, W, pad), cy = yPix(p.y, vmin, vmax, H, pad);
    var pinned = state.pins.indexOf(p.m.id) >= 0;
    var col = tierColor(p.m.tier, pinned);
    if (pinned || p.m.tier === "AA_REFERENCE") {
      s += '<g data-i="' + i + '" style="cursor:pointer"><rect x="' + (cx-5) + '" y="' + (cy-5) +
        '" width="10" height="10" transform="rotate(45 ' + cx + ' ' + cy + ')" fill="' + col + '"/></g>';
    } else {
      s += '<g data-i="' + i + '" style="cursor:pointer"><circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) +
        '" r="' + (p.m.tier === "PROVIDER_CANDIDATE" ? "7" : "6") + '" fill="' + col + '"/></g>';
    }
  });
  svg.innerHTML = s;
  var groups = svg.querySelectorAll("g[data-i]");
  groups.forEach(function(g){
    g.addEventListener("mouseenter", function(){ showHud(pts[+g.getAttribute("data-i")].m, g); });
    g.addEventListener("click", function(){ showHud(pts[+g.getAttribute("data-i")].m, g); });
    g.addEventListener("mouseleave", function(){ document.getElementById("hud").style.display = "none"; });
  });
  svg._pts = pts;
}
function showHud(m, g){
  var hud = document.getElementById("hud");
  var flag = byId[DATA.flagship_id];
  var delta = "";
  if (flag && flag.id !== m.id && m.scores.intelligence != null && flag.scores.intelligence != null) {
    var d = m.scores.intelligence - flag.scores.intelligence;
    delta = '<div>D vs ' + esc(flag.display) + ': <b>' + (d >= 0 ? "+" : "") + d.toFixed(1) + ' pts</b></div>';
  }
  hud.innerHTML = '<b>' + esc(m.display) + '</b> <span class="mono" style="color:var(--muted)">' + esc(m.provider) + '</span>' +
    '<div class="row"><span>Intelligence ' + (m.scores.intelligence != null ? m.scores.intelligence : "\\u2014") + '</span>' +
    '<span>' + fmtPrice(m.price_per_1m) + '</span>' +
    '<span>' + (m.scores.ttft_ms != null ? m.scores.ttft_ms + "ms" : "\\u2014") + '</span></div>' + delta +
    '<button class="btn" style="margin-top:.3rem" data-stage="' + esc(m.id) + '">+ Stage in YAML</button>';
  hud.style.display = "block";
  var chart = document.getElementById("chart").getBoundingClientRect();
  var r = g.getBoundingClientRect();
  var x = r.left - chart.left + 16, y = r.top - chart.top - 30;
  hud.style.left = Math.min(Math.max(x, 4), chart.width - 310) + "px";
  hud.style.top = Math.max(y, 4) + "px";
  hud.querySelector("[data-stage]").addEventListener("click", function(){
    if (state.staged.indexOf(m.id) < 0) state.staged.push(m.id);
    renderStaged();
  });
}
function tierBadge(tier, pinned){
  if (pinned || tier === "AA_REFERENCE") return '<span class="badge b-ref">AA Reference</span>';
  if (tier === "PROVIDER_CANDIDATE") return '<span class="badge b-cand">Candidate</span>';
  if (tier === "AA_INDEX") return '<span class="badge b-aa">AA Index</span>';
  return '<span class="badge b-proxy">In Proxy</span>';
}
function getStatusLabel(m){
  var pinned = state.pins.indexOf(m.id) >= 0;
  if (pinned || m.tier === "AA_REFERENCE") return "AA Reference";
  if (m.tier === "PROVIDER_CANDIDATE") return "Candidate";
  if (m.tier === "AA_INDEX") return "AA Index";
  return "In Proxy";
}
function updateSortHeaders(){
  document.querySelectorAll("#matrix th[data-sort]").forEach(function(th){
    var col = th.getAttribute("data-sort");
    var icon = th.querySelector(".sort-icon");
    if (!icon) return;
    if (state.sortCol === col) {
      icon.textContent = state.sortDir === "asc" ? " ▲" : " ▼";
    } else {
      icon.textContent = " ↕";
    }
  });
}
function renderTable(){
  updateSortHeaders();
  var tb = document.querySelector("#matrix tbody");
  var rows = filtered();
  if (state.sortCol) {
    rows.sort(function(a, b){
      var va, vb;
      if (state.sortCol === "model") {
        va = (a.display || a.model || "").toLowerCase();
        vb = (b.display || b.model || "").toLowerCase();
      } else if (state.sortCol === "status") {
        va = getStatusLabel(a).toLowerCase();
        vb = getStatusLabel(b).toLowerCase();
      } else if (state.sortCol === "intelligence" || state.sortCol === "coding" || state.sortCol === "agentic") {
        va = a.scores[state.sortCol];
        vb = b.scores[state.sortCol];
      }
      if (va === vb) {
        var sa = (a.display || a.model || "").toLowerCase();
        var sb = (b.display || b.model || "").toLowerCase();
        return sa < sb ? -1 : (sa > sb ? 1 : 0);
      }
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      var cmp = (va < vb) ? -1 : (va > vb ? 1 : 0);
      return state.sortDir === "asc" ? cmp : -cmp;
    });
  }
  var renderedRows = rows.map(function(m){
    var pinned = state.pins.indexOf(m.id) >= 0;
    var action;
    if (pinned || m.tier === "AA_REFERENCE") {
      action = '<button class="btn ghost" data-unpin="' + esc(m.id) + '">Unpin</button>';
    } else if (m.tier === "AA_INDEX") {
      action = '<button class="btn ghost" data-pin="' + esc(m.id) + '">+ Pin</button>';
    } else if (m.tier === "PROVIDER_CANDIDATE") {
      action = '<button class="btn ghost" data-stage="' + esc(m.id) + '">+ Stage</button>';
    } else {
      action = '<span class="mono" style="color:#a5b4fc">Routing Active</span>';
    }
    function cell(v, dim){
      var hl = (dim === state.dim) ? ' style="background:#6366f122"' : '';
      return '<td class="num"' + hl + '>' + v + '</td>';
    }
    var ttft = m.scores.ttft_ms != null ? m.scores.ttft_ms.toFixed(0) + "ms" : "\\u2014";    return '<tr><td><b>' + esc(m.display) + '</b><div class="mono" style="color:var(--muted)">' +
      esc(m.provider_id) + (m.tag_tier ? ' \\u00b7 ' + esc(m.tag_tier) : '') + '</div></td>' +
      '<td>' + tierBadge(m.tier, pinned) + '</td>' +
      cell(m.scores.intelligence != null ? m.scores.intelligence : "\\u2014", "intelligence") +
      cell(m.scores.coding != null ? m.scores.coding : "\\u2014", "coding") +
      cell(m.scores.agentic != null ? m.scores.agentic : "\\u2014", "agentic") +
      cell(ttft) +
      '<td class="num">' + fmtPrice(m.price_per_1m) + '</td><td>' + action + '</td></tr>';
  });
  tb.innerHTML = renderedRows.length ? renderedRows.join("") :
    '<tr><td colspan="8" class="muted">No rows match. Adjust the filter or tabs.</td></tr>';
  tb.querySelectorAll("[data-stage]").forEach(function(b){
    b.addEventListener("click", function(){
      if (state.staged.indexOf(b.getAttribute("data-stage")) < 0) state.staged.push(b.getAttribute("data-stage"));
      renderStaged();
    });
  });
  tb.querySelectorAll("[data-unpin]").forEach(function(b){
    b.addEventListener("click", function(){ unpin(b.getAttribute("data-unpin")); });
  });
  tb.querySelectorAll("[data-pin]").forEach(function(b){
    b.addEventListener("click", function(){ pin(b.getAttribute("data-pin")); });
  });
}
function renderPins(){
  var sel = document.getElementById("pinSelect");
  var html = '<optgroup label="Models">';
  html += DATA.reference_catalog.map(function(e){
    return '<option value="' + esc(e.slug) + '">' + esc(e.name) + '</option>';
  }).join("");
  html += '</optgroup>';

  var refSetKeys = Object.keys(DATA.reference_sets || {});
  if (refSetKeys.length > 0) {
    html += '<optgroup label="Reference Sets">';
    html += refSetKeys.map(function(key) {
      return '<option value="refset/' + esc(key) + '">' + esc(DATA.reference_sets[key].display_name || key) + '</option>';
    }).join("");
    html += '</optgroup>';
  }

  sel.innerHTML = html;

  var box = document.getElementById("chips");
  box.innerHTML = state.pins.map(function(id){
    var label = id;
    if (id.indexOf("refset/") === 0) {
        var setId = id.slice(7);
        if (DATA.reference_sets && DATA.reference_sets[setId]) {
            label = "Set: " + (DATA.reference_sets[setId].display_name || setId);
        }
    } else {
        var m = byId[id];
        label = m ? m.display : id;
    }
    return '<span class="chip">\\u25C6 ' + esc(label) +
      ' <button data-unpin="' + esc(id) + '">\\u00d7</button></span>';
  }).join("") || '<span class="muted">Nothing pinned.</span>';
  box.querySelectorAll("[data-unpin]").forEach(function(b){
    b.addEventListener("click", function(){ unpin(b.getAttribute("data-unpin")); });
  });
}
function persistPins(){ try { localStorage.setItem("radar_pins", JSON.stringify(state.pins)); } catch(e) {} }
function pin(id){
  id = canonicalPinId(id);
  if (!id || state.pins.indexOf(id) >= 0) return;
  state.pins.push(id);
  persistPins(); renderAll();
}
function unpin(id){
  state.pins = state.pins.filter(function(p){ return p !== id; });
  persistPins(); renderAll();
}
function renderInsights(){
  var box = document.getElementById("insights");
  if (!DATA.recommendations.length) {
    box.innerHTML = '<div class="muted">No algorithmic insights from current snapshot.</div>';
    return;
  }
  box.innerHTML = DATA.recommendations.map(function(r){
    return '<h3>' + esc(r.tag) + '</h3><div><b>' + esc(r.title) + '</b></div>' +
      '<div class="muted">' + esc(r.summary) + '</div>' +
      '<button class="btn ghost" style="margin-top:.3rem" data-stage="' + esc(r.target) + '">Stage target</button>';
  }).join("");
  box.querySelectorAll("[data-stage]").forEach(function(b){
    b.addEventListener("click", function(){
      if (state.staged.indexOf(b.getAttribute("data-stage")) < 0) state.staged.push(b.getAttribute("data-stage"));
      renderStaged();
    });
  });
}
function renderStaged(){
  var btn = document.getElementById("exportBtn");
  btn.textContent = state.staged.length ? "Export YAML Diff (" + state.staged.length + ")" : "Export YAML Diff";
}
function yamlFor(m){
  var slug = (m.provider_id || m.model).split("/").slice(-1)[0].replace(/[^a-zA-Z0-9-_]/g, "-").toLowerCase();
  return "  - model_name: " + slug + "\\n    litellm_params:\\n      model: " +
    (m.provider || "openai") + "/" + m.provider_id + "\\n";
}
function renderAll(){ renderScatter(); renderTable(); renderPins(); renderInsights(); renderStaged(); }

document.querySelectorAll("#matrix th[data-sort]").forEach(function(th){
  th.addEventListener("click", function(){
    var col = th.getAttribute("data-sort");
    if (state.sortCol === col) {
      state.sortDir = (state.sortDir === "asc") ? "desc" : "asc";
    } else {
      state.sortCol = col;
      state.sortDir = (col === "model" || col === "status") ? "asc" : "desc";
    }
    renderTable();
  });
});
document.getElementById("q").addEventListener("input", function(e){ state.q = e.target.value; renderScatter(); renderTable(); });
document.getElementById("dim").addEventListener("change", function(e){ state.dim = e.target.value; renderAll(); });
document.getElementById("scaleLog").addEventListener("click", function(){
  state.log = true;
  document.getElementById("scaleLog").classList.add("active");
  document.getElementById("scaleLin").classList.remove("active");
  renderScatter();
});
document.getElementById("scaleLin").addEventListener("click", function(){
  state.log = false;
  document.getElementById("scaleLin").classList.add("active");
  document.getElementById("scaleLog").classList.remove("active");
  renderScatter();
});
document.querySelectorAll("#tabs button").forEach(function(b){
  b.addEventListener("click", function(){
    document.querySelectorAll("#tabs button").forEach(function(x){ x.classList.remove("active"); });
    b.classList.add("active");
    state.tab = b.getAttribute("data-tab");
    renderScatter(); renderTable();
  });
});
document.getElementById("pinBtn").addEventListener("click", function(){
  var slug = document.getElementById("pinSelect").value;
  if (slug.indexOf("refset/") === 0) {
    pin(slug);
  } else {
    var ref = DATA.models.filter(function(m){ return m.provider_id === slug || m.model === slug || m.arch === slug; })[0];
    pin(ref ? ref.id : "reference/" + slug);
  }
});
document.getElementById("exportBtn").addEventListener("click", function(){
  var out = "model_list:\\n" + state.staged.map(function(id){
    var m = byId[id];
    return m ? yamlFor(m) : ("  # unknown staged id: " + id + "\\n");
  }).join("");
  document.getElementById("yamlOut").textContent = state.staged.length ? out : "# Nothing staged. Use + Stage buttons first.";
  document.getElementById("modal").style.display = "flex";
});
document.getElementById("closeBtn").addEventListener("click", function(){
  document.getElementById("modal").style.display = "none";
});
document.getElementById("copyBtn").addEventListener("click", function(){
  var t = document.getElementById("yamlOut").textContent;
  if (navigator.clipboard) navigator.clipboard.writeText(t);
});
document.getElementById("modal").addEventListener("click", function(e){
  if (e.target.id === "modal") document.getElementById("modal").style.display = "none";
});
renderMeta();
renderAll();
</script>
</body>
</html>"""
