"""Domain logic for autonomous agent runtime models and the Interactions API."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import yaml

from model_manager.config import AppConfig
from model_manager.domain import auth, storage, tags as tags_mod

DEFAULT_LITELLM_URL = "http://localhost:4000"
DEFAULT_TIMEOUT_SEC = 180
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def list_agents(config: AppConfig, include_unmapped: bool = False) -> list[dict[str, Any]]:
    """List agent runtime models.

    Pulls mapped agent models from models.json (variants with tag 'agent').
    If include_unmapped is True, also discovers candidate agent models from
    provider caches (e.g. gemini_available_models.json).
    """
    data = storage.load_models_data(config)
    agents: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()

    for mid, m_info in data.get("models", {}).items():
        for vid, v_info in m_info.get("variants", {}).items():
            if tags_mod.is_agent_variant(v_info):
                prov_map = v_info.get("provider_ids", {}) or {}
                for pname, pids_entry in prov_map.items():
                    limits = {}
                    if isinstance(pids_entry, dict):
                        limits = pids_entry.get("limits") or {}
                        for pid, pdata in pids_entry.items():
                            if pid in ("include_in_litellm", "limits"):
                                continue
                            seen_model_ids.add(str(pid))
                            avail = pdata.get("availability", 1.0) if isinstance(pdata, dict) else 1.0
                            assessment = pdata.get("assessment", "Good") if isinstance(pdata, dict) else "Good"
                            agents.append({
                                "alias": mid,
                                "variant": vid,
                                "display_name": m_info.get("display_name", mid),
                                "provider": pname.lower(),
                                "model_id": str(pid),
                                "target": f"{pname.lower()}/{pid}",
                                "endpoint": "/v1beta/interactions",
                                "status": assessment,
                                "availability": avail,
                                "limits": limits or v_info.get("limits") or {},
                                "mapped": True,
                                "notes": v_info.get("notes", ""),
                            })

    if include_unmapped:
        gemini_cache = config.data_dir / "gemini_available_models.json"
        if gemini_cache.exists():
            try:
                cdata = json.loads(gemini_cache.read_text())
                for m in cdata.get("models", []):
                    mid = m.get("id", "")
                    if ("antigravity" in mid.lower() or "agent" in m.get("name", "").lower()) and mid not in seen_model_ids:
                        agents.append({
                            "alias": "-",
                            "variant": "-",
                            "display_name": m.get("name", mid),
                            "provider": "gemini",
                            "model_id": mid,
                            "target": f"gemini/{mid}",
                            "endpoint": "/v1beta/interactions",
                            "status": "Discovered",
                            "availability": None,
                            "limits": {},
                            "mapped": False,
                            "notes": m.get("description", ""),
                        })
            except (json.JSONDecodeError, OSError):
                pass

    return agents


def resolve_agent_target(config: AppConfig, agent_query: str) -> dict[str, Any]:
    """Resolve an agent alias or model ID to its execution parameters."""
    known_agents = list_agents(config, include_unmapped=True)
    q = agent_query.strip().lower()

    # 1. Match against mapped agents
    for a in known_agents:
        if a["alias"].lower() == q or a["model_id"].lower() == q or a["target"].lower() == q:
            return a

    # 2. Check router stub aliases
    if config.litellm_router_settings_stub_path.exists():
        try:
            doc = yaml.safe_load(config.litellm_router_settings_stub_path.read_text()) or {}
            aliases = (doc.get("router_settings") or {}).get("model_group_alias") or {}
            if q in aliases:
                target = aliases[q]
                provider, _, pid = target.partition("/")
                return {
                    "alias": q,
                    "variant": "standard",
                    "display_name": q.replace("-", " ").title(),
                    "provider": provider.lower() if provider else "gemini",
                    "model_id": pid or target,
                    "target": target,
                    "endpoint": "/v1beta/interactions",
                    "status": "Configured",
                    "availability": 1.0,
                    "limits": {},
                    "mapped": True,
                    "notes": "",
                }
        except Exception:
            pass

    # Default fallback
    target_pid = agent_query.split("/")[-1]
    provider = agent_query.split("/")[0] if "/" in agent_query else "gemini"
    return {
        "alias": agent_query,
        "variant": "standard",
        "display_name": agent_query,
        "provider": provider,
        "model_id": target_pid,
        "target": f"{provider}/{target_pid}",
        "endpoint": "/v1beta/interactions",
        "status": "Unknown",
        "availability": None,
        "limits": {},
        "mapped": False,
        "notes": "",
    }


def resolve_gemini_api_key(config: AppConfig) -> str | None:
    """Resolve an active Gemini API key from keychain or environment."""
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = auth.get_secret(env_name)
        if val:
            return val

    # Try configured provider keys from config.toml
    gemini_cfg = config.providers.get("gemini")
    if gemini_cfg and gemini_cfg.keys:
        for k_name in gemini_cfg.keys:
            val = auth.get_secret(k_name)
            if val:
                return val

    return None


def resolve_proxy_master_key(config: AppConfig) -> str | None:
    """Resolve the LiteLLM proxy master key."""
    val = auth.get_secret("LITELLM_MASTER_KEY")
    if val:
        return val

    # Try parsing litellm.yaml
    if config.litellm_config_path.exists():
        try:
            doc = yaml.safe_load(config.litellm_config_path.read_text()) or {}
            key = (doc.get("general_settings") or {}).get("master_key")
            if key and not key.startswith("os.environ/"):
                return key
            if key and key.startswith("os.environ/"):
                env_var = key.replace("os.environ/", "")
                return auth.get_secret(env_var)
        except Exception:
            pass

    return None


def run_interaction(
    config: AppConfig,
    agent: str = "antigravity",
    prompt: str = "",
    *,
    tools: list[str] | None = None,
    environment: str = "remote",
    previous_interaction_id: str | None = None,
    direct: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    debug: bool = False,
) -> dict[str, Any]:
    """Execute an agent interaction via the /v1beta/interactions endpoint."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    target_info = resolve_agent_target(config, agent)
    tool_list = tools if tools is not None else ["code_execution"]
    tool_payload = [{"type": t} for t in tool_list]

    payload: dict[str, Any] = {
        "agent": target_info["model_id"] if direct else (target_info["alias"] or target_info["model_id"]),
        "environment": environment,
        "input": prompt,
        "tools": tool_payload,
    }
    if previous_interaction_id:
        payload["previous_interaction_id"] = previous_interaction_id

    headers = {
        "Content-Type": "application/json",
    }

    if direct:
        resolved_key = api_key or resolve_gemini_api_key(config)
        if not resolved_key:
            raise RuntimeError(
                "Gemini API key not found. Set GEMINI_API_KEY in environment or "
                "store it in keychain: model-manager auth set GEMINI_KEY_TIMALOSI=<key>"
            )
        request_url = f"{GEMINI_INTERACTIONS_URL}?key={resolved_key}"
    else:
        proxy_url = base_url or DEFAULT_LITELLM_URL
        resolved_key = api_key or resolve_proxy_master_key(config)
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        request_url = f"{proxy_url.rstrip('/')}/v1beta/interactions"

    body_bytes = json.dumps(payload).encode("utf-8")

    debug_info = None
    if debug:
        # Mask api key in URL for debug display
        safe_url = request_url.split("?key=")[0] if "?key=" in request_url else request_url
        debug_info = {
            "url": safe_url,
            "headers": {k: ("Bearer ***" if k.lower() == "authorization" else v) for k, v in headers.items()},
            "payload": payload,
        }

    req = urllib.request.Request(
        request_url,
        data=body_bytes,
        headers=headers,
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            return {
                "success": True,
                "status_code": response.getcode(),
                "latency_ms": latency_ms,
                "agent": payload["agent"],
                "interaction_id": data.get("interaction_id"),
                "status": data.get("status", "completed"),
                "outputs": data.get("outputs", []),
                "raw_response": data,
                "debug": debug_info,
            }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
        except Exception:
            err_json = {"raw": err_body}
        return {
            "success": False,
            "status_code": e.code,
            "latency_ms": latency_ms,
            "agent": payload["agent"],
            "interaction_id": None,
            "status": "error",
            "error": str(e.reason),
            "detail": err_json,
            "raw_response": err_body,
            "debug": debug_info,
        }
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "success": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "agent": payload["agent"],
            "interaction_id": None,
            "status": "error",
            "error": str(e),
            "detail": None,
            "raw_response": None,
            "debug": debug_info,
        }
