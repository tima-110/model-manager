# Handoff: LiteLLM Configuration Enhancements for Model Fallback & Key Observability

> **Target Repository**: `~/projects/model-manager`
> **Status**: Implemented
> **Related Projects**: `~/projects/tux-ai`, `~/projects/Argos-Dashboard`
> **LiteLLM Version**: `v1.105.1` (running container)

---

## 1. Executive Summary & Objective

`model-manager` is the centralized generator for all LiteLLM configuration files in `/etc/litellm/` (`litellm-*-test.yaml`, `litellm-router_settings.yaml`, `litellm-fallbacks.yaml`, etc.).

Previously, generated YAML deployment entries contained `model_name` and `litellm_params` but omitted explicit deployment IDs. LiteLLM then auto-hashed each deployment, producing an opaque 64-character SHA-256 `model_id` in `LiteLLM_SpendLogs`. Downstream consumers such as the Argos Financial Dashboard had to maintain reverse-lookup hash tables to determine which physical API key served a request.

The objective is met by stamping a stable, human-readable `model_info.id` on every generated deployment, so `LiteLLM_SpendLogs.model_id` identifies **provider + model + API key** directly.

---

## 2. Change 1: Explicit `model_info.id` (implemented)

### File: `src/model_manager/domain/yaml_gen.py`

`derive_deployment_id()` builds the ID from the LiteLLM provider prefix, the provider's model ID, and the API-key environment variable:

```python
def derive_deployment_id(
    litellm_prefix: str, provider_id: str, key_env_var: str
) -> str:
    return f"{litellm_prefix}/{provider_id}::{key_env_var}"
```

Each generated entry now carries:

```yaml
model_list:
  - model_name: "gemini/gemini-3.5-flash-lite"
    litellm_params:
      model: "gemini/gemini-3.5-flash-lite"
      api_key: "os.environ/GEMINI_KEY_CASTOR"
      rpm: 15
      tpm: 250000
    model_info:
      id: "gemini/gemini-3.5-flash-lite::GEMINI_KEY_CASTOR"
```

### Design decisions

- The ID includes the provider prefix, so the same model served by two providers cannot collide (e.g. `gemma-4-31b-it` on `nvidia_nim` vs `gemini`).
- The key's **environment variable name** is used; no secret value is written to YAML or logs.
- `key_name` is intentionally omitted. LiteLLM does not persist it to `LiteLLM_SpendLogs`; the ID already identifies the key.
- Generation raises `RuntimeError` on duplicate IDs (duplicate keys or the same `provider_id` mapped by multiple variants).

### Why this works in LiteLLM

`Router.set_model_list()` preserves an explicit `model_info.id` and skips `generate_model_id()`:

```python
_model_info: dict = model.pop("model_info", {})
if "id" not in _model_info:
    _model_info["id"] = self.generate_model_id(_model_name, _litellm_params)
```

The router records that ID as `router_model_id`, and `LiteLLM_SpendLogs.model_id` is populated from it. `model_id` is a `text` column, so the longer readable IDs are safe.

---

## 3. Change 2: Fallback Error Headers (manual `general_settings`)

### File: `/etc/litellm/litellm.yaml`

Add one line to the existing `general_settings` block:

```yaml
general_settings:
  master_key: "<existing value, unchanged>"
  expose_fallback_errors_to_caller: true
```

`general_settings` is intentionally **not generated** by `model-manager`: it is a stable, manually managed block and does not change on the weekly generation cadence.

### Important: per-request opt-in

`expose_fallback_errors_to_caller: true` is necessary but not sufficient. LiteLLM only attaches fallback error details when the **request body also sends**:

```json
{ "include_fallback_errors": true }
```

Behavior:

| Header | Requires global flag | Requires request opt-in |
|---|---|---|
| `x-litellm-attempted-fallbacks` | No | No |
| `x-litellm-fallback-errors` | Yes | Yes |

Downstream clients (Argos, LangGraph, Hindsight) must send `include_fallback_errors: true` to receive the error array. Without it, only the attempted-fallback count is exposed.

### Why this is not an included YAML

LiteLLM include merging is **top-level and shallow**: a non-list key in an included file replaces the base value. An include containing only `general_settings` would wipe `master_key`. Keeping the block in `litellm.yaml` avoids that hazard and matches the existing manual-management approach.

---

## 4. Change 3: Alias Fallback Chains (already implemented)

`model-manager` already emits fallback chains for both the canonical model group and its alias:

- `router_settings.mirror_alias_fallbacks()` copies the target's chain under each stub alias.
- Generated `litellm-fallbacks.yaml` therefore includes entries such as `gemini-model`, `gemini-flash`, `deepseek`, and `hermes-default`.

Regression coverage lives in `tests/test_router_settings.py`
(`test_stub_alias_mirrors_target_chain`, `test_mirrored_alias_passes_generate_validation`).

No production code change was required.

---

## 5. Implementation & Verification

### Tests

```bash
.venv/bin/python -m pytest \
  tests/test_yaml_gen.py \
  tests/test_litellm.py \
  tests/test_router_settings.py \
  tests/test_blocked_provider.py
```

### Dry run and write

```bash
model-manager litellm generate all --dry-run
model-manager litellm generate all
model-manager litellm config check
```

`model-manager litellm config check` now warns (without failing) when
`expose_fallback_errors_to_caller` is absent from the merged config.

### Restart

```bash
docker restart tux-ai-litellm
# or
model-manager litellm restart request
```

### End-to-end check

Send a request using the master key from the environment. Never paste the key
into a document or command history:

```bash
curl -s -X POST http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-model",
    "messages": [{"role": "user", "content": "ping"}],
    "include_fallback_errors": true
  }'
```

Then verify the spend log:

```bash
docker exec tux-ai-postgres psql -U postgres -d litellm -c "
SELECT request_id, model, model_id, metadata->>'original_model_group' AS alias, \"startTime\"
FROM \"LiteLLM_SpendLogs\"
ORDER BY \"startTime\" DESC
LIMIT 1;
"
```

Expected:
- `model`: `gemini/gemini-3.5-flash-lite`
- `model_id`: `gemini/gemini-3.5-flash-lite::GEMINI_KEY_CASTOR` (or another configured key)
- `alias`: `gemini-model`

---

## 6. Backward Compatibility

- **Existing data**: historical SHA-256 IDs remain in `LiteLLM_SpendLogs`; the Argos translation dictionary still covers them.
- **Routing**: `model_info.id` does not change model names, rate limits, or routing logic.
- **Clients**: response schemas are unchanged. Fallback error details are additive and opt-in per request.
- **Secrets**: generated YAML references keys only as `os.environ/...`. No key values belong in this document or any handoff artifact.
