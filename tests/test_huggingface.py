"""Tests for the HuggingFace provider (free warm models, HF_TOKEN auth)."""
from __future__ import annotations

import io
import json
import urllib.request

import yaml

from model_manager.config import AppConfig, ProviderConfig, get_huggingface_models_path
from model_manager.domain import discovery, providers, yaml_gen


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch, payload: bytes, capture: dict | None = None):
    def fake_urlopen(req, *args, **kwargs):
        if capture is not None:
            capture["url"] = req.full_url if isinstance(req, urllib.request.Request) else req
            if isinstance(req, urllib.request.Request):
                capture["headers"] = dict(req.header_items())
                capture["data"] = req.data
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_fetch_huggingface_models_maps_hub_schema(monkeypatch):
    """Hub API entries map to the internal model dict shape."""
    hub = [
        {"id": "meta-llama/Llama-3.1-8B-Instruct", "tags": ["text-generation", "conversational"]},
        {"id": "Qwen/Qwen2.5-7B-Instruct", "tags": []},
        {"no-id": True},
    ]
    capture: dict = {}
    _patch_urlopen(monkeypatch, json.dumps(hub).encode(), capture)

    models = discovery.fetch_huggingface_models("hf_test")

    assert len(models) == 2
    assert models[0]["id"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert models[0]["name"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert models[0]["tags"] == ["text-generation", "conversational"]
    assert "Authorization" in {k.lower().capitalize(): v for k, v in capture["headers"].items()} or capture["headers"]
    auth_headers = {k.lower(): v for k, v in capture["headers"].items()}
    assert auth_headers.get("authorization") == "Bearer hf_test"
    assert "huggingface.co/api/models" in capture["url"]
    assert "inference=warm" in capture["url"]


def test_provider_registered_with_hf_token():
    """HuggingFace is listed with HF_TOKEN secret and conservative scan defaults."""
    by_name = {p.name.lower(): p for p in providers.list_providers()}
    hf = by_name["huggingface"]
    assert hf.secret_key == "HF_TOKEN"
    assert hf.probe_id == "huggingface"
    assert hf.fetch_fn is discovery.fetch_huggingface_models
    assert hf.scan_concurrency == 1
    assert hf.scan_delay_between_models_ms == 600


def test_huggingface_cache_path(tmp_path):
    cfg = AppConfig(data_dir=tmp_path)
    assert get_huggingface_models_path(cfg).name == "huggingface_available_models.json"


def test_probe_huggingface_uses_router(monkeypatch):
    """probe_model routes huggingface through the HF router with Bearer auth."""
    body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()

    class Resp(_FakeResponse):
        def getcode(self):
            return 200

    capture: dict = {}

    def fake_urlopen(req, *args, **kwargs):
        capture["url"] = req.full_url
        capture["headers"] = {k.lower(): v for k, v in req.header_items()}
        capture["payload"] = json.loads(req.data.decode())
        return Resp(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = discovery.probe_model("meta-llama/Llama-3.1-8B-Instruct", "hf_test", provider="huggingface")

    assert result.status == "up"
    assert capture["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert capture["headers"]["authorization"] == "Bearer hf_test"
    assert capture["payload"]["model"] == "meta-llama/Llama-3.1-8B-Instruct"


def test_yaml_gen_huggingface_prefix(tmp_path):
    """LiteLLM generation derives huggingface/ names from org/model ids."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models = {
        "models": {
            "llama-31-8b": {
                "display_name": "Llama 3.1 8B",
                "family": "meta",
                "default_variant": "standard",
                "variants": {
                    "standard": {
                        "aa_slug": "llama-3.1-8b",
                        "provider_ids": {"huggingface": ["meta-llama/Llama-3.1-8B-Instruct"]},
                    }
                },
            }
        }
    }
    (data_dir / "models.json").write_text(json.dumps(models))
    cfg = AppConfig(
        data_dir=data_dir,
        providers={
            "huggingface": ProviderConfig(
                keys=["HF_TOKEN"],
                output_path=tmp_path / "out.yaml",
                litellm_prefix="huggingface",
            )
        },
    )
    result = yaml_gen.generate_provider_yaml(cfg, "huggingface", dry_run=True)
    parsed = yaml.safe_load(result)
    entry = parsed["model_list"][0]
    assert entry["model_name"] == "huggingface/Llama-3.1-8B-Instruct"
    assert entry["litellm_params"]["model"] == "huggingface/meta-llama/Llama-3.1-8B-Instruct"
    assert entry["litellm_params"]["api_key"] == "os.environ/HF_TOKEN"
    assert "api_base" not in entry["litellm_params"]
