# Plan: `litellm generate-config` command

## Overview

Generate per-provider LiteLLM YAML config files from models.json, provider scan results, and key/param configuration.

## Data Sources

| Source | Purpose |
|--------|---------|
| `models.json` | Selected models with variants + `provider_ids` per provider |
| `{provider}_scan.json` | Per-model health status from last scan — filter out `unauthorized`, include everything else |
| `config.toml` (new sections) | Per-provider: API key env vars, output path, standard params (rpm, api_base), litellm prefix |

## Config.toml Additions

```toml
[providers.nvidia]
keys = ["NVIDIA_KEY_TIMALOSI", "NVIDIA_KEY_CASTOR", "NVIDIA_KEY_POLLUX"]
output_path = "/etc/litellm/litellm-nvidia.yaml"
litellm_prefix = "nvidia_nim"
rpm = 40

[providers.gemini]
keys = ["GEMINI_KEY_TIMALOSI", "GEMINI_KEY_TIM", "GEMINI_KEY_CASTOR", "GEMINI_KEY_A", "GEMINI_KEY_POLLUX"]
output_path = "/etc/litellm/litellm-gemini.yaml"
litellm_prefix = "gemini"
rpm = 15

[providers.ollama]
keys = ["OLLAMA_KEY_TIMALOSI", "OLLAMA_KEY_POLLUX", "OLLAMA_CASTOR"]
output_path = "/etc/litellm/litellm-ollama.yaml"
litellm_prefix = "ollama"
api_base = "https://ollama.com"

[providers.openrouter]
keys = ["OPENROUTER_KEY_TIMALOSI"]
output_path = "/etc/litellm/litellm-openrouter.yaml"
litellm_prefix = "openrouter"
```

## Model Name Derivation

`model_name` = `{litellm_prefix}/{short_slug}` where `short_slug` is the last segment of the provider_id (after the last `/`).

`litellm_params.model` = `{litellm_prefix}/{provider_id}` (full provider_id with any org prefix).

**Edge case**: `nvidia/nvidia/nemotron-3-ultra-550b-a55b` → short_slug = `nemotron-3-ultra-550b-a55b` but hand-authored yaml uses `nemotron-3-ultra`. I propose stripping `-\d+b\S*` (parameter-count suffixes) from short_slug as a heuristic, and adding an optional `litellm_slug` override per variant in models.json for manual control.

## Status Filtering

From `{provider}_scan.json` → for each model, read `models[provider_id].summary.assessment`:
- **Skip**: `unauthorized`
- **Include**: everything else (`up`, `down`, `not_found`, `unsupported`, `timeout`, `ratelimit`)

## YAML Output Structure (per provider)

```yaml
model_list:
  - model_name: {litellm_prefix}/{short_slug}
    litellm_params:
      model: {litellm_prefix}/{provider_id}
      api_key: os.environ/{KEY_1}
      rpm: {rpm}
      # optional: api_base: {api_base}

  - model_name: {litellm_prefix}/{short_slug}
    litellm_params:
      model: {litellm_prefix}/{provider_id}
      api_key: os.environ/{KEY_2}
      rpm: {rpm}
```

Each (model, variant, provider) combination is replicated for every configured API key.

## Implementation

### Files to create/modify

| File | Action | Purpose |
|------|--------|---------|
| `src/model_manager/config.py` | Modify `AppConfig` | Add `ProviderOutputConfig` model and per-provider config |
| `src/model_manager/domain/yaml_gen.py` | **New** | Core logic: read models.json, filter by scan, build YAML |
| `src/model_manager/cli/litellm.py` | Modify | Add `generate-config` command |
| `tests/` | New tests | Coverage for yaml_gen logic |

### Architecture

```
CLI: litellm generate-config [--provider nvidia|gemini|ollama|openrouter]
                            [--output PATH] [--config CONFIG]
  → yaml_gen.generate_provider_yaml(config, provider_name)
    → load models.json
    → for each model's variants where provider_ids[provider_name] exists:
        → look up scan results → skip if unauthorized
        → derive short_slug and litellm model name
        → for each configured API key:
            → emit YAML entry with standard params
    → write YAML file
```

### Questions for the user

1. **Nemotron name edge case**: the hand-authored yaml uses `nvidia_nim/nemotron-3-ultra` but provider_id is `nvidia/nemotron-3-ultra-550b-a55b`. Should the heuristic strip `-\d+b\S*` (parameter count) suffixes automatically, or prefer a manual `litellm_slug` override?

2. **Scan without recent data**: what if no scan results exist yet for a provider? Skip health filtering entirely (include all) or error out?

3. **Dry-run mode**: useful to preview the YAML without writing to `/etc/litellm/`? E.g., `litellm generate-config --provider gemini --dry-run`