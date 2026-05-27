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

## Data Storage
The tool maintains six primary JSON files in the `data_dir`.

### 1. `model_scores.json`
Contains the processed intelligence, coding, and math scores fetched from Artificial Analysis.

**Schema:**
- `meta`: Metadata about the sync (timestamp, source, total models).
- `models`: A dictionary keyed by the **AA Slug**.
    - `name`: Human-readable model name.
    - `scores`: A dictionary of indices (`intelligence`, `coding`, `math`).
    - `last_synced`: Timestamp of the last update.

### 2. `model_aliases.json`
The mapping layer that translates provider-specific IDs into AA slugs.

**The Variant-Based Schema:**
The tool uses a hierarchical structure to handle model variants (e.g., quantized vs. full precision).

- `models`: A dictionary keyed by the **Conceptual Model ID**.
    - `display_name`: Human-readable name.
    - `family`: The organization/creator (e.g., `google`, `meta`).
    - `variants`: A dictionary of variants (e.g., `standard`, `quantized-low`).
        - `aa_slug`: The slug used to look up scores in `model_scores.json`.
        - `provider_ids`: A mapping of providers to lists of IDs.
            - `google`: `["google/gemma-4-31b-it"]`
            - `openrouter`: `["gemma-4-31b-it:free"]`
        - `notes`: Context for why this variant is used.
    - `default_variant`: The fallback variant if no specific match is found.

### 3. `aa_raw_response.json`
A direct dump of the Artificial Analysis API response. This is used for debugging and for recovering data if the processing logic changes.

### 4. `openrouter_free_models.json`
A cache of free models discovered from the OpenRouter API, including their context length and architecture.

### 5. `nvidia_available_models.json`
A cache of models discovered from the NVIDIA API.

### 6. `ollama_available_models.json`
A cache of models discovered from the Ollama Cloud API.

## Resolution Flow
When `model-manager aliases resolve <id>` is called, the following logic is applied:
1. **Explicit Search**: The system scans all variants in `model_aliases.json` for the provided ID.
2. **Model Match**: If the ID matches a conceptual model key, the `default_variant` is used.
3. **Score Lookup**: The resolved `aa_slug` is used to fetch the laest scores from `model_scores.json`.

### Forward Resolution
The tool also supports resolving a **Conceptual Model ID** instead of a provider ID. In this mode, the system:
1. Looks up the model ID directly in the `models` map of `model_aliases.json`.
2. Retrieves the model's metadata (family, display name).
3. Lists all associated variants and the provider IDs mapped to each.
