# Configuration Guide

This document describes the configuration and data storage for `model-manager`.

## Global Configuration
`model-manager` uses a TOML configuration file located at `~/.config/model-manager/config.toml` (determined via `platformdirs`).

### Settings
| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `data_dir` | Path | `platformdirs.user_data_dir` | Directory where JSON state files are stored. |
| `verbose` | Boolean | `false` | Enables verbose output to stdout. |
| `debug` | Boolean | `false` | Enables debug-level logging to stderr. |
| `scan_frequency` | Integer | `5` | Delay in seconds between health-scan cycles (overridable per provider via `cycle_delay_sec`). |
| `scan_count` | Integer | `24` | Default number of scan cycles when `--max-scans` is not given. |
| `litellm_service_dir` | Path | `/var/www/local_json_data` | Directory where the merged cost map is written for LiteLLM. |
| `litellm_config_path` | Path | `/etc/litellm/litellm.yaml` | LiteLLM config file checked by `litellm config check`. |
| `litellm_fallbacks_path` | Path | `/etc/litellm/litellm-fallbacks.yaml` | Default output for `litellm generate fallbacks`. |
| `litellm_aliases_path` | Path | `/etc/litellm/litellm-aliases.yaml` | Default output for `litellm generate aliases`. |
| `litellm_router_settings_stub_path` | Path | `/etc/litellm/litellm-router_settings-stub.yaml` | Hand-managed stub read by `litellm generate router_settings` (never overwritten). |
| `litellm_router_settings_path` | Path | `/etc/litellm/litellm-router_settings.yaml` | Default output for `litellm generate router_settings`. |
| `litellm_restart_request_path` | Path | `/etc/litellm/restart_requests.jsonl` | File monitored by LiteLLM service for restart requests. |
| `litellm_cost_map_url` | String | `https://raw.githubusercontent.com/BerriAI/litellm/refs/heads/litellm_internal_staging/model_prices_and_context_window.json` | Source URL for the upstream cost map. |
| `tags.tier1_min_ratio` | Float | `0.85` | Minimum composite score (as a fraction of the library leader) for Tier 1. |
| `tags.tier2_min_ratio` | Float | `0.70` | Minimum composite score (as a fraction of the library leader) for Tier 2. |
| `schedule.enabled` | Boolean | `false` | Set by `schedule install` / `schedule remove`; whether the background schedule is active. |
| `schedule.frequency` | String | `"daily"` | One of `daily`, `hourly`, `weekly`. |
| `schedule.day` | String | `"monday"` | Day of week for `weekly` frequency (name or abbreviation). |
| `schedule.time` | String | `"02:00"` | Firing time in `HH:MM` (daily/weekly; hourly uses the minute). |
| `schedule.max_scans` | Integer | `2` | Scan cycles per provider per scheduled run. |
| `dashboard.enabled` | Boolean | `true` | Master toggle for dashboard artifact settings. |
| `dashboard.out_dir` | Path | _(unset)_ | Directory for the HTML artifact (unset falls back to `data_dir`). |
| `dashboard.out_file` | String | `"dashboard.html"` | File name for the HTML artifact (empty falls back to `dashboard.html`). |
| `dashboard.git_enabled` | Boolean | `false` | Master toggle for git publish (must default OFF). |
| `dashboard.git_branch` | String | `""` | Target remote branch name (empty pushes current branch upstream). |
| `radar.enabled` | Boolean | `true` | Master toggle for radar artifact settings. |
| `radar.out_dir` | Path | _(unset)_ | Directory for the radar HTML artifact (unset falls back to `data_dir`). |
| `radar.out_file` | String | `"radar.html"` | File name for the radar HTML artifact (empty falls back to `radar.html`). |
| `radar.git_enabled` | Boolean | `false` | Master toggle for radar git publish (must default OFF). |
| `radar.git_branch` | String | `""` | Target remote branch name (empty pushes current branch upstream). |
| `radar.pinned_references` | List of strings | `[]` | AA slugs pre-pinned as baselines on the radar page (empty by default). |
| `blocking.block_signals` | List of strings | (see below) | Observed statuses/codes that block a target from generated configs. |

Default `block_signals`: `unauthorized`, `forbidden`, `not_found`, `gone`, `unsupported`, `payment_required`, `401`, `403`, `404`, `402`, `410` (matched case-insensitively against scan assessments, probe statuses, and HTTP codes). Remove a token to stop blocking on it. Fetch-absence always blocks regardless of this list. The first clean observation releases a target.

### Availability Blocks (`model_blocks.json`)

Fetch and scan observations feed a persistent ledger in `data_dir/model_blocks.json` that decides what the generators emit:

- **User exclusions** (`include_in_litellm: false`) always win and never enter the ledger.
- **Fetch-absence** (mapped ID no longer listed by the provider) blocks; reappearance releases. `models.json` mappings are never pruned.
- **Provider scan assessments** in the block list (e.g. unauthorized, not_found, unsupported) block; healthy labels release; transient labels (`dead`, `ratelimited`, `unknown`) are tracked but never block.
- **LiteLLM probe results**: `up` releases; block-listed statuses/codes block; timeouts, 500s, `empty`, and rate limits are tracked but never block.
- Inspect via `doctor` (blocked-models section, with billing issues flagged separately) and the dashboard's Availability Blocks section; the raw JSON holds `{blocked_since, reason, code, source}` per key.

Tier ratios can be overridden per-invocation with `--t1-ratio` / `--t2-ratio` on `models tag tier`.

### Per-Provider Sections (`[providers.xxx]`)

`litellm generate config` and the scan workflow read per-provider settings from
`[providers.nvidia]`, `[providers.gemini]`, `[providers.ollama]`,
`[providers.openrouter]`, and `[providers.huggingface]` sections. A provider is included in `--all-providers`
generation only when it has both `keys` and `litellm_prefix` set.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `keys` | List of strings | `[]` | Environment variable names holding API keys (one generated config entry per key). |
| `output_path` | Path | `None` | Override for the generated YAML file for this provider. |
| `litellm_prefix` | String | `""` | Prefix used to derive `model_name` (`{prefix}/{short-slug}`) and `litellm_params.model` (`{prefix}/{provider_id}`). |
| `rpm` | Integer | `None` | Optional requests-per-minute limit written into generated entries. |
| `api_base` | String | `None` | Optional API base URL written into `litellm_params`. |
| `scan_concurrency` | Integer | `None` (provider default) | Override for parallel scan requests. |
| `scan_delay_between_models_ms` | Integer | `None` (provider default) | Override for delay between per-model scan requests. |
| `cycle_delay_sec` | Integer | `None` (falls back to `scan_frequency`) | Delay between scan cycles for this provider. |

Example:

```toml
[providers.nvidia]
keys = ["NVIDIA_API_KEY"]
output_path = "/etc/litellm/litellm-nvidia.yaml"
litellm_prefix = "nvidia_nim"

[providers.ollama]
keys = ["OLLAMA_API_KEY"]
output_path = "/etc/litellm/litellm-ollama.yaml"
litellm_prefix = "ollama"
api_base = "https://ollama.com"

[providers.huggingface]
keys = ["HF_TOKEN"]
output_path = "/etc/litellm/litellm-huggingface.yaml"
litellm_prefix = "huggingface"
```

### Per-Tier Provider Hierarchy (`[tier_providers.tierN]`)

`litellm generate aliases` reads an ordered provider preference per tier.
Names refer to `[providers.xxx]` keys. Tier membership is always decided
first (from `tier-1`/`tier-2`/`tier-3` tags, computed from scores when tags
are missing); the provider order only picks *which* in-tier variant wins,
so a tier alias never points at another tier's model.

```toml
[tier_providers.tier1]
provider_order = ["nvidia", "gemini", "openrouter"]

[tier_providers.tier2]
provider_order = ["nvidia", "gemini", "openrouter"]

[tier_providers.tier3]
provider_order = ["gemini", "nvidia", "openrouter"]
```

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `provider_order` | List of strings | `[]` (= nvidia first, then remaining configured providers) | Preference order for the tier's alias. Unknown names are ignored with a warning; if no listed provider backs the tier, the best-composite in-tier variant on any provider is used. |

### Scheduled Service Secrets (`model-manager-schedule.env`)

`schedule install` exports the keychain entries stored under this app's service name into a `0600` env file so timer firings (possibly without a login session) can authenticate:

- Linux: `~/.config/systemd/user/model-manager-schedule.env` (next to the unit files)
- macOS: `~/Library/LaunchAgents/model-manager-schedule.env` (next to the plist)

The service consumes it via `EnvironmentFile=` and `schedule run --env-file`; interactive runs without `--env-file` keep using the keychain (explicit environment always wins over the file, keychain remains the fallback). Refresh after key changes with `auth update-env` (or re-run `schedule install`); `schedule remove` deletes the file.

## Data Storage
The tool maintains nine primary JSON files in the `data_dir`, plus per-provider
scan results and generated LiteLLM artifacts (see below).

### 1. `model_scores.json`
Contains the processed intelligence, coding, and agentic scores fetched from Artificial Analysis, plus speed metrics.

**Schema:**
- `meta`: Metadata about the sync (timestamp, source, total models).
- `models`: A dictionary keyed by the **AA Slug**.
    - `name`: Human-readable model name.
    - `scores`: A dictionary of indices (`intelligence`, `coding`, `agentic`) and speed metrics (`ttft` in seconds, `tps` in tokens/second).
    - `last_synced`: Timestamp of the last update.

The `agentic` index is served by the AA language-models API (`artificial_analysis_agentic_index`)
and merged in by `scores fetch` (`merge_agentic_scores`). Models without an API value keep
their previously saved agentic score.

### 2. `models.json`
The mapping layer that translates provider-specific IDs into AA slugs.

**The Variant-Based Schema:**
The tool uses a hierarchical structure to handle model variants (e.g., quantized vs. full precision).

- `models`: A dictionary keyed by the **Conceptual Model ID**.
    - `display_name`: Human-readable name.
    - `family`: The organization/creator (e.g., `google`, `meta`).
    - `variants`: A dictionary of variants (e.g., `standard`, `quantized-low`).
        - `aa_slug`: The slug used to look up scores in `model_scores.json`.
        - `scores`: (Optional) A snapshot of the AA scores (`intelligence`, `coding`, `agentic`, `ttft`, `tps`) at the time of allocation.
        - `tags`: (Optional) A list of tags. `models tag tier` manages `tier-1`/`tier-2`/`tier-3` tags here; other tags are preserved.
        - `include_in_litellm`: (Optional) Set to `false` to exclude the whole variant from `litellm generate config` (see `models variant update --litellm-disable/--litellm-enable`).
        - `provider_ids`: A mapping of provider name to a dictionary of provider ID → scan metadata.
            - `openrouter`: `{ "openrouter/gemma-4-31b-it:free": { "availability": 0.98, "avg_latency": 320.5, "assessment": "Good", "scan_timestamp": "..." }, ... }`
            - A per-provider `include_in_litellm: false` key inside a provider's dictionary excludes that provider for the variant (see `models variant update --litellm-disable-provider/--litellm-enable-provider`).
            - Newly mapped IDs start as empty objects (`{}`) until a `providers ... scan` populates their health data. Legacy list-format mappings are auto-migrated to this shape on write.
        - `notes`: Context for why this variant is used.
    - `default_variant`: The fallback variant if no specific match is found.

### Auto-Tiering
`models tag tier` automatically classifies every scored variant into `tier-1`, `tier-2`, or `tier-3`:

1. Compute a **composite score** = average of the numeric values among `intelligence`, `coding`, and `agentic` (variants with none of these are skipped).
2. Find the **leader**: the highest composite score in the library.
3. Assign tiers relative to the leader:
   - `tier-1`: composite ≥ `tier1_min_ratio` × leader (default `0.85`)
   - `tier-2`: composite ≥ `tier2_min_ratio` × leader (default `0.70`)
   - `tier-3`: everything else.

Because tiers are relative to the current best model, classifications automatically track models as their scores improve over time. Only `tier-*` tags are rewritten; manually added tags are never touched.

### 3. `aa_raw_response.json`
A direct dump of the Artificial Analysis API response. This is used for debugging and for recovering data if the processing logic changes.

### 4. `openrouter_free_models.json`
A cache of free models discovered from the OpenRouter API, including their context length and architecture.

### 5. `nvidia_available_models.json`
A cache of models discovered from the NVIDIA API.

### 6. `ollama_available_models.json`
A cache of models discovered from the Ollama Cloud API.

### 7. `gemini_available_models.json`
A cache of models discovered from the Gemini API.

### 8. `huggingface_available_models.json`
A cache of free (warm, serverless `text-generation`) models discovered from the HuggingFace Hub API.

### 9. `litellm_cost_overrides.json`
A local map used to override upstream LiteLLM pricing or context windows. This file is merged with the upstream GitHub JSON during a `litellm cost-map build`.

**Schema:**
A dictionary where keys are **Model IDs** and values are partial or full model metadata blocks.
- `_meta`: (Optional) Metadata about the local overrides.
- `model-id`: Dictionary containing fields to override (e.g., `input_cost_per_token`, `max_tokens`).

### 10. `model_prices.json`
The local price library read by the radar page (`radar` never fetches prices itself). Built by `prices fetch` from local overrides first, then the OpenRouter full model catalog, then the upstream LiteLLM cost map (first source wins per key).

**Schema:**
- `meta`: Sync metadata (timestamp, sources, total entries).
- `prices`: A dictionary keyed by **lowercased model id or AA slug**.
    - `blended_per_1m`: Blended USD price per 1M tokens (mean of prompt + completion).
    - `source`: One of `override`, `openrouter`, `litellm-upstream`.

### Scan Results and Generated Artifacts

These files are produced by scan/generate commands rather than discovery:

- `{provider}_scan.json` (e.g., `openrouter_scan.json`, `nvidia_scan.json`, `ollama_scan.json`, `gemini_scan.json`, `huggingface_scan.json`): per-model ping history plus a summary (`availability`, `avg_latency`, `assessment`) written by `providers ... scan` / `providers scan-all`. Scan summaries for mapped IDs are also copied into `models.json` under `provider_ids`.
- `debug_scan_{provider}_{timestamp}.json`: request/response logs written when scanning with `--debug`.
- `model_prices_and_context_window.json`: merged upstream + overrides cost map written to `litellm_service_dir` by `litellm cost-map build`.
- Generated LiteLLM YAML: written to each provider's `output_path` (or `--output`) by `litellm generate config`, to `litellm_fallbacks_path` (or `--output`) by `litellm generate fallbacks`, to `litellm_aliases_path` (or `--output`) by `litellm generate aliases`, and to `litellm_router_settings_path` (or `--output`) by `litellm generate router_settings` (which reads the stub at `litellm_router_settings_stub_path` and never overwrites it). Alternatively, `litellm generate all` generates all provider configs, fallbacks, aliases, and router_settings at once, and can optionally build the cost map with `--build-cost-map`.
- `restart_requests.jsonl` (at `litellm_restart_request_path`): append-only log of `{"timestamp", "reason", "requested_by"}` restart requests written by `litellm request-restart` (and each successful schedule run). Consumed by the LiteLLM-side watcher — never append foreign record types here.
- `schedule_runs.jsonl` (in `data_dir`): append-only per-run record of `{timestamp, steps, errors, error_count}` written by every `schedule run`. The timer-facing status history.
- `model_blocks.json` (in `data_dir`): availability ledger (see Availability Blocks above).
- `dashboard.html` (in `data_dir`): status dashboard written by `dashboard` and regenerated last by each schedule run.
- `radar.html` (in `data_dir`): static interactive benchmark radar written by `radar` (Pareto scatter, pin drawer, matrix, YAML draft export).
- `litellm_scan.json` (in `data_dir`): per-model proxy probe records (`kind`, `status`, `ttft_ms`, `tps`, `tpm_est`, token counts, `reasoning_chunks`/`reasoning_chars`, `finish_reason`, `attempts`) plus a run summary, written by `litellm scan`. Status `empty` means HTTP 200 with neither content nor reasoning chunks; an `empty` first attempt is retried once at 4x token headroom (thinking models share the cap between reasoning and content). Targets come from the generated YAMLs plus every alias in the merged router_settings file; shown as a table on the dashboard.

## Resolution Flow
When `model-manager aliases resolve <id>` is called, the following logic is applied:
1. **Explicit Search**: The system scans all variants in `models.json` for the provided ID.
2. **Model Match**: If the ID matches a conceptual model key, the `default_variant` is used.
3. **Score Lookup**: The resolved `aa_slug` is used to fetch the latest scores from `model_scores.json`.

### Forward Resolution
The tool also supports resolving a **Conceptual Model ID** instead of a provider ID. In this mode, the system:
1. Looks up the model ID directly in the `models` map of `models.json`.
2. Retrieves the model's metadata (family, display name).
3. Lists all associated variants and the provider IDs mapped to each.
