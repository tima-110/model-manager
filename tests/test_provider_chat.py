"""Tests for provider `chat` command, domain chat_model, and interactive model picker."""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_manager.cli import app
from model_manager.cli import common as cli_common
from model_manager.config import AppConfig
from model_manager.domain import auth, discovery, providers

runner = CliRunner()


class _FakeResponse:
    def __init__(self, payload: bytes, code: int = 200):
        self._payload = payload
        self._code = code

    def getcode(self) -> int:
        return self._code

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, payload: dict, capture: dict | None = None):
    raw_bytes = json.dumps(payload).encode()

    def fake_urlopen(req, *args, **kwargs):
        if capture is not None:
            capture["url"] = req.full_url
            capture["headers"] = dict(req.header_items())
            capture["data"] = json.loads(req.data.decode())
            capture["kwargs"] = kwargs
        return _FakeResponse(raw_bytes)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


# --- Domain tests: chat_model & probe_model ---

def test_chat_model_nvidia_openai_compatible(monkeypatch: pytest.MonkeyPatch):
    capture: dict = {}
    _patch_urlopen(
        monkeypatch,
        {
            "choices": [{"message": {"content": "Hello from NVIDIA!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 25, "total_tokens": 35},
        },
        capture,
    )

    res = discovery.chat_model(
        "meta/llama-3.1-8b-instruct",
        "nvapi-test",
        "Hi there",
        provider="nvidia",
    )

    assert res.text == "Hello from NVIDIA!"
    assert res.code == "200"
    assert res.prompt_tokens == 10
    assert res.completion_tokens == 25
    assert res.total_tokens == 35
    assert res.finish_reason == "stop"
    assert res.tokens_per_sec is not None and res.tokens_per_sec > 0
    assert capture["url"] == discovery.NVIDIA_CHAT_URL
    assert capture["headers"]["Authorization"] == "Bearer nvapi-test"
    assert capture["data"] == {
        "model": "meta/llama-3.1-8b-instruct",
        "messages": [{"role": "user", "content": "Hi there"}],
    }
    assert "timeout" not in capture["kwargs"]


def test_chat_model_gemini_endpoint_and_schema(monkeypatch: pytest.MonkeyPatch):
    capture: dict = {}
    _patch_urlopen(
        monkeypatch,
        {
            "candidates": [{"content": {"parts": [{"text": "Hello from "}, {"text": "Gemini!"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 12, "totalTokenCount": 20},
        },
        capture,
    )

    res = discovery.chat_model(
        "gemini-2.5-flash",
        "gem-key",
        "Explain gravity",
        provider="gemini",
        timeout=15.0,
    )

    assert res.text == "Hello from Gemini!"
    assert res.prompt_tokens == 8
    assert res.completion_tokens == 12
    assert res.total_tokens == 20
    assert res.finish_reason == "STOP"
    assert capture["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=gem-key"
    assert "Authorization" not in capture["headers"]
    assert capture["data"] == {"contents": [{"parts": [{"text": "Explain gravity"}]}]}
    assert capture["kwargs"]["timeout"] == 15.0


def test_chat_model_ollama_endpoint_and_schema(monkeypatch: pytest.MonkeyPatch):
    capture: dict = {}
    _patch_urlopen(
        monkeypatch,
        {
            "message": {"role": "assistant", "content": "Hello from Ollama!"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 40,
            "eval_duration": 500_000_000,
        },
        capture,
    )

    res = discovery.chat_model(
        "llama3.2",
        "ollama-key",
        "Ping",
        provider="ollama",
    )

    assert res.text == "Hello from Ollama!"
    assert res.prompt_tokens == 5
    assert res.completion_tokens == 40
    assert res.total_tokens == 45
    assert res.tokens_per_sec == 80.0
    assert res.finish_reason == "stop"
    assert capture["url"] == discovery.OLLAMA_CHAT_URL
    assert capture["data"]["stream"] is False
    assert capture["data"]["messages"] == [{"role": "user", "content": "Ping"}]


def test_probe_model_still_uses_max_tokens_1(monkeypatch: pytest.MonkeyPatch):
    capture: dict = {}
    _patch_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": "x"}}]},
        capture,
    )

    ping = discovery.probe_model("meta/llama-3.1-8b-instruct", "nvapi-test", provider="nvidia")
    assert ping.status == "up"
    assert capture["data"]["max_tokens"] == 1
    assert capture["data"]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_model_errors(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(RuntimeError, match="API key missing"):
        discovery.chat_model("m", None, "hi", provider="nvidia")

    def raise_http(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error": "model not found"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    with pytest.raises(RuntimeError, match="HTTP 404 from nvidia"):
        discovery.chat_model("bad-model", "nvapi-test", "hi", provider="nvidia")

    def raise_timeout(req, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    with pytest.raises(RuntimeError, match="timed out after 5.0s"):
        discovery.chat_model("m", "nvapi-test", "hi", provider="nvidia", timeout=5.0)


# --- Interactive picker tests ---

def test_read_terminal_key_escape_sequences(monkeypatch: pytest.MonkeyPatch):
    r_fd, w_fd = os.pipe()
    try:
        fake_stdin = os.fdopen(r_fd, "r", buffering=1)
        monkeypatch.setattr(cli_common.sys, "stdin", fake_stdin)
        monkeypatch.setattr(cli_common.termios, "tcgetattr", lambda fd: [])
        monkeypatch.setattr(cli_common.termios, "tcsetattr", lambda fd, when, attrs: None)
        monkeypatch.setattr(cli_common.tty, "setraw", lambda fd: None)

        for seq, expected in [
            (b"\x1b[A", "UP"),
            (b"\x1b[B", "DOWN"),
            (b"\x1bOA", "UP"),
            (b"\x1bOB", "DOWN"),
            (b"\x1b[5~", "PGUP"),
            (b"\x1b[6~", "PGDN"),
            (b"\x1b[C", "UNKNOWN"),
            (b"\r", "ENTER"),
            (b"\x1b", "ESC"),
        ]:
            os.write(w_fd, seq)
            assert cli_common._read_terminal_key() == expected
    finally:
        os.close(w_fd)
        fake_stdin.close()


def test_interactive_select_model_navigation_and_filter():
    models = [
        {"id": "meta/llama-3.1-8b", "name": "Llama 3.1 8B"},
        {"id": "qwen/qwen2.5-72b", "name": "Qwen 2.5 72B"},
        {"id": "mistralai/mistral-large", "name": "Mistral Large"},
    ]

    # Navigate down once and press Enter -> selects second model
    keys = iter(["DOWN", "ENTER"])
    selected = cli_common._interactive_select_model(
        models, "NVIDIA", "2026-01-01T00:00:00Z", read_key=lambda: next(keys)
    )
    assert selected == "qwen/qwen2.5-72b"

    # Type "mis", backspace, type "s", press Enter -> matches mistral
    keys_filter = iter(["m", "i", "s", "BACKSPACE", "s", "ENTER"])
    selected_filtered = cli_common._interactive_select_model(
        models, "NVIDIA", "2026-01-01T00:00:00Z", read_key=lambda: next(keys_filter)
    )
    assert selected_filtered == "mistralai/mistral-large"

    # Esc cancels and returns None
    keys_esc = iter(["ESC"])
    assert cli_common._interactive_select_model(
        models, "NVIDIA", "2026-01-01T00:00:00Z", read_key=lambda: next(keys_esc)
    ) is None


def test_render_model_picker_shows_fetched_at_date():
    panel = cli_common._render_model_picker(
        [{"id": "m1"}], [{"id": "m1"}], 0, "", "NVIDIA", "2025-05-01T12:00:00Z"
    )
    assert "2025-05-01T12:00:00Z" in str(panel.renderable)


def test_pick_provider_model_uses_stale_cache_and_checks_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = AppConfig(data_dir=tmp_path)
    nvidia = next(p for p in providers.list_providers() if p.name == "NVIDIA")

    # Missing cache -> Exit 1
    with pytest.raises(Exception):
        cli_common._pick_provider_model(nvidia, cfg)

    # Write an old/stale cache (should NOT block)
    cache_path = nvidia.path_fn(cfg)
    cache_path.write_text(json.dumps({
        "fetched_at": "2024-01-01T00:00:00Z",
        "models": [{"id": "meta/llama-3.1-8b"}],
    }))

    # Non-TTY -> Exit 1
    monkeypatch.setattr(cli_common.sys.stdin, "isatty", lambda: False)
    with pytest.raises(Exception):
        cli_common._pick_provider_model(nvidia, cfg)

    # TTY -> passes fetched_at to _interactive_select_model without blocking on age
    monkeypatch.setattr(cli_common.sys.stdin, "isatty", lambda: True)
    seen: dict = {}

    def fake_select(models, prov_name, fetched_at):
        seen["fetched_at"] = fetched_at
        return models[0]["id"]

    monkeypatch.setattr(cli_common, "_interactive_select_model", fake_select)
    picked = cli_common._pick_provider_model(nvidia, cfg)
    assert picked == "meta/llama-3.1-8b"
    assert seen["fetched_at"] == "2024-01-01T00:00:00Z"


# --- CLI tests ---

def test_cli_providers_chat_with_model_and_prompt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "get_secret", lambda key: "test-key")
    called: dict = {}

    def fake_chat_model(model_id, api_key, prompt, provider="openrouter", timeout=None):
        called.update(model_id=model_id, api_key=api_key, prompt=prompt, provider=provider, timeout=timeout)
        return discovery.ChatResult(
            text="Hi back!",
            latency_ms=1234.0,
            code="200",
            raw={"ok": True},
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            tokens_per_sec=16.2,
            finish_reason="stop",
        )

    monkeypatch.setattr(discovery, "chat_model", fake_chat_model)

    result = runner.invoke(app, ["providers", "nvidia", "chat", "--model", "meta/llama-3.1-8b", "-p", "hello"])
    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "Hi back!" in normalized
    assert "1.23 s" in normalized
    assert "tokens: 10 in / 20 out (30 total)" in normalized
    assert "16.2 tok/s" in normalized
    assert "finish: stop" in normalized
    assert called == {
        "model_id": "meta/llama-3.1-8b",
        "api_key": "test-key",
        "prompt": "hello",
        "provider": "nvidia",
        "timeout": None,
    }


def test_cli_providers_chat_prompts_for_prompt_and_uses_picker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "get_secret", lambda key: "test-key")
    monkeypatch.setattr(cli_common, "_pick_provider_model", lambda prov, cfg: "picked/model")
    called: dict = {}

    def fake_chat_model(model_id, api_key, prompt, provider="openrouter", timeout=None):
        called.update(model_id=model_id, prompt=prompt, provider=provider, timeout=timeout)
        return discovery.ChatResult(text="Picked response", latency_ms=50.0, code="200", raw={})

    monkeypatch.setattr(discovery, "chat_model", fake_chat_model)

    result = runner.invoke(
        app,
        ["providers", "gemini", "chat", "--timeout", "12.5"],
        input="What is 2+2?\n",
    )
    assert result.exit_code == 0
    assert "Picked response" in result.stdout
    assert called == {
        "model_id": "picked/model",
        "prompt": "What is 2+2?",
        "provider": "gemini",
        "timeout": 12.5,
    }


def test_cli_providers_chat_json_and_missing_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "get_secret", lambda key: None)
    res_no_key = runner.invoke(app, ["providers", "nvidia", "chat", "-m", "m1", "-p", "hi"])
    assert res_no_key.exit_code == 1
    assert "NVIDIA_API_KEY missing" in res_no_key.stdout

    monkeypatch.setattr(auth, "get_secret", lambda key: "test-key")
    monkeypatch.setattr(
        discovery,
        "chat_model",
        lambda **kwargs: discovery.ChatResult(text="JSON hi", latency_ms=42.0, code="200", raw={"id": "1"}),
    )
    res_json = runner.invoke(app, ["providers", "openrouter", "chat", "-m", "m1", "-p", "hi", "--json"])
    assert res_json.exit_code == 0
    parsed = json.loads(res_json.stdout)
    assert parsed["provider"] == "openrouter"
    assert parsed["model"] == "m1"
    assert parsed["response"] == "JSON hi"
    assert parsed["raw"] == {"id": "1"}
