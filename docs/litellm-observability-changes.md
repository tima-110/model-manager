# LiteLLM Configuration Enhancements: Model Fallback & Key Observability

This is a short mirror of [PROPOSED_CONFIG_CHANGES_HANDOFF.md](../PROPOSED_CONFIG_CHANGES_HANDOFF.md). See the handoff for full details.

- **Deployment IDs**: generated entries now include `model_info.id` formatted as `{litellm_prefix}/{provider_id}::{KEY_ENV_VAR}`, so `LiteLLM_SpendLogs.model_id` identifies provider + model + key.
- **Fallback errors**: `expose_fallback_errors_to_caller: true` is a manual, one-line addition to `general_settings` in `/etc/litellm/litellm.yaml`, not a generated include. Clients must also send `"include_fallback_errors": true` to receive `x-litellm-fallback-errors`.
- **Alias fallbacks**: already generated via `router_settings.mirror_alias_fallbacks()`; no code change needed.
- **Secrets**: generated YAML uses `os.environ/...` references only. Do not record key values in documentation or prompts.
