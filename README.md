# model-manager

A professional CLI tool for managing model performance rankings and identifier aliases for LiteLLM installations.

## Prerequisites
- Python $\ge$ 3.12
- `pipx` installed
- An Artificial Analysis API Key

## Installation
Install the tool in editable mode for development:
```bash
pipx install -e .
```

## Quick Start
1. **Set your API Keys**: Use the secure keychain storage:
   ```bash
   model-manager auth set ARTIFICIAL_ANALYSIS_API_KEY=your_key_here
   ```
2. **Initialize the tool**:
   ```bash
   model-manager init
   ```
3. **Sync latest rankings**:
   ```bash
   model-manager scores sync
   ```
4. **Map your LiteLLM models**:
   ```bash
   model-manager sync-litellm --config /etc/litellm/litellm.yaml
   ```
5. **Check a model's score**:
   ```bash
   model-manager aliases resolve "openrouter/gpt-4o-mini:free"
   ```

## Command Reference

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `init` | Initialize data directories | `--config` |
| `sync-litellm` | Sync mappings from LiteLLM config | `--config`, `--provider` |
| `scores sync` | Fetch latest rankings from AA | `--config` |
| `aliases resolve` | Resolve a provider ID or conceptual model ID to details and scores | `provider_id` |
| `aliases add` | Add or update a model mapping (supports skeleton models) | `provider`, `id`, `model`, `variant`, `family`, `display_name`, `aa_slug`, `--config` |
| `aliases discover` | Suggest mappings for unmapped IDs | `provider`, `ids`, `--config` |
| `aliases audit` | Report mapping coverage | `ids`, `--config` |
| `models discover` | Guided discovery and mapping of provider IDs to a conceptual model | `model_id`, `--provider`, `--refresh`, `--yolo`, `--config` |
| `auth set` | Store an API key in keychain | `key_name`, `value` |
| `auth delete` | Remove an API key from keychain | `key_name` |
| `auth list` | List stored keys | *(none)* |
| `advisor compare` | Compare multiple models side-by-side | `ids`, `--config` |
| `advisor best` | Find best model for a metric | `--metric`, `--config` |
| `advisor gaps` | Report mapping gaps for IDs | `ids`, `--config` |
| `discover-free` | Discover free OpenRouter models | `--probe`, `--config` |
| `discover-nvidia` | Discover NVIDIA available models | `--probe`, `--config` |
| `discover-ollama` | Discover Ollama available models | `--probe`, `--config` |

## Guided Discovery Workflow

The `model discover` command provides an interactive, three-phase workflow to map providers to performance identities:

1. **Model Identification**: Search the AA dataset to find and assign the correct `aa_slug` for the model's default variant.
2. **Variant Definition**: Optionally define additional functional variants (e.g., "fast", "cheap") and associate them with specific AA slugs.
3. **Provider Mapping**: 
   - **Strong Matches**: Automatically identified via AA for variants with slugs.
   - **Suggested Matches**: Fuzzy-matched from local provider caches (OpenRouter, NVIDIA, Ollama). Users can assign these to variants or skip them.

Scores (Intelligence, Coding, Math) are snapshotted directly into the variant data during this process for fast local analysis.

## Model Mapping Architecture

The tool uses a hierarchical mapping system to group different provider IDs under a single performance identity:

```text
ROOT (JSON Object)
└── "conceptual-model-id" (e.g., "gemma-4-31b-it")
    ├── display_name: "Gemma 4 31B IT"
    ├── family: "google"
    ├── default_variant: "standard"
    └── variants (Object)
        └── "variant-name" (e.g., "standard", "quantized-low")
            ├── aa_slug: "gemma-4-31b-it"  <--- [ LINK TO model_scores.json ]
            ├── notes: "Official FP16 version"
            └── provider_ids (Object)
                ├── "google"
                │   └── [ "google/gemma-4-31b-it" ]
                ├── "openrouter"
                │   └── [ "openrouter/gemma-4-31b-it:free", "openrouter/gemma-4-31b-it" ]
                └── "nvidia"
                    └── [ "nvidia/gemma-4-31b-it" ]
```

This allows the system to perform a **reverse lookup**: it finds a provider ID in the tree and then "climbs up" to resolve the model's performance scores via the `aa_slug`.

## Configuration
Detailed configuration options can be found in the [Configuration Guide](docs/config-guide.md).
