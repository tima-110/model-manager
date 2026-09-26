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
1. **Store your API keys** (OpenRouter, Artificial Analysis, NVIDIA, Ollama, Gemini, HuggingFace):
   ```bash
   model-manager auth set ARTIFICIAL_ANALYSIS_API_KEY=your_key_here
   model-manager auth list
   ```
2. **Fetch latest benchmark scores** and sync them into your library:
   ```bash
   model-manager scores fetch
   model-manager scores sync
   ```
3. **Discover provider models**:
   ```bash
   model-manager providers fetch-all
   # or a single provider:
   model-manager providers openrouter fetch
   ```
4. **Map a model to its performance identity** (guided workflow):
   ```bash
   model-manager models discover my-model
   ```
5. **Auto-assign tier tags** relative to the library leader:
   ```bash
   model-manager models tag tier
   ```
6. **Generate LiteLLM configs**:
   ```bash
   model-manager litellm generate config --all-providers
   model-manager litellm generate fallbacks
   ```
7. **Check health and view status**:
   ```bash
   model-manager doctor
   model-manager dashboard --no-open
   ```

## Global Options

These flags work on the root command and most subcommands:

| Flag | Description |
| :--- | :--- |
| `--version`, `-V` | Show version and exit. |
| `--config`, `-c PATH` | Path to a custom `config.toml`. |
| `--verbose` | Enable verbose output (root command). |
| `--help` | Show help for any command or subcommand. |
| `--json` | Where offered, emit machine-readable JSON instead of a Rich table. |

## Command Reference

### `models` — Manage the conceptual model library

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `models list` | List conceptual models with variants, providers, and scan status | `--config`, `--json` |
| `models add MODEL` | Add or update a conceptual model | `MODEL` (required); `--family/-f`, `--display-name/-d`, `--default-variant/-v`, `--config`, `--json` |
| `models remove MODEL` | Remove a conceptual model from the library | `MODEL` (required); `--config`, `--json` |
| `models discover MODEL_ID` | Guided discovery and mapping of provider IDs to a conceptual model | `MODEL_ID` (required); `--provider/-p`, `--refresh`, `--yolo`, `--config` |
| `models variant update MODEL VARIANT` | Include/exclude a variant (or one provider of a variant) from LiteLLM generation | `MODEL`, `VARIANT` (required); `--litellm-disable`, `--litellm-enable`, `--litellm-disable-provider TEXT`, `--litellm-enable-provider TEXT`, `--config` |
| `models tag tier` | Auto-assign `tier-1/2/3` tags to scored variants relative to the library leader | `--dry-run`, `--t1-ratio`, `--t2-ratio`, `--config`, `--json` |
| `models tag list` | List current tags per variant | `--config`, `--json` |
| `models tag set MODEL VARIANT TAG` | Manually add a tag to a variant | `MODEL`, `VARIANT`, `TAG`; `--config`, `--json` |
| `models tag remove MODEL VARIANT TAG` | Remove a tag from a variant | `MODEL`, `VARIANT`, `TAG`; `--config`, `--json` |

### `scores` — Manage Artificial Analysis score ingestion

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `scores fetch` | Fetch latest scores from Artificial Analysis (also scrapes agentic index) | `--config` |
| `scores sync` | Update model variants in `models.json` from the local score cache | `--config` |
| `scores list` | List scores in a table | `--filter/-f`, `--refresh`, `--selected-models`, `--config`, `--json` |

`--selected-models` limits the list to models in `models.json` with an `aa_slug`. `--filter` matches name or slug. `--refresh` re-fetches from the API before listing.

### `aliases` — Manage model identifier mappings

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `aliases resolve IDENTIFIER` | Resolve a provider ID or conceptual model ID to details and scores | `IDENTIFIER` (required); `--config` |
| `aliases add MODEL` | Add/update a mapping; omit provider flags to create a skeleton model | `MODEL` (required); `--variant` (default `standard`), `--family`, `--display-name`, `--aa-slug`, `--provider`, `--provider-id`, `--config` |
| `aliases discover PROVIDER IDS` | Suggest mappings for unmapped IDs (comma-separated) | `PROVIDER`, `IDS` (required); `--config` |
| `aliases audit IDS` | Report mapping coverage for a comma-separated ID list | `IDS` (required); `--config` |

### `advisor` — High-level model selection and comparison

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `advisor compare IDS` | Compare multiple models side-by-side (Intel/Coding/Agentic) | `IDS` comma-separated (required); `--config` |
| `advisor best` | Find best mapped model for a metric | `--metric` (default `intelligence`; `intelligence`, `coding`, `agentic`), `--config` |
| `advisor gaps IDS` | Report mapping gaps for a comma-separated ID list | `IDS` (required); `--config` |

### `providers` — Manage supported model providers

Top-level batch commands plus one `fetch`/`scan` pair per provider (`openrouter`, `nvidia`, `ollama`, `gemini`, `huggingface`).

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `providers fetch-all` | Query all supported providers and save capabilities | `--probe`, `--config`, `--json` |
| `providers scan-all` | Scan health/performance of all supported providers | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |
| `providers openrouter fetch` | Query current free OpenRouter models | `--probe`, `--config`, `--json` |
| `providers openrouter scan` | Scan health of OpenRouter models | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |
| `providers nvidia fetch` | Query available NVIDIA models | `--probe`, `--config`, `--json` |
| `providers nvidia scan` | Scan health of NVIDIA models | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |
| `providers ollama fetch` | Query available Ollama Cloud models | `--probe`, `--config`, `--json` |
| `providers ollama scan` | Scan health of Ollama models | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |
| `providers gemini fetch` | Query available Gemini models | `--probe`, `--config`, `--json` |
| `providers gemini scan` | Scan health of Gemini models | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |
| `providers huggingface fetch` | Query free HuggingFace models | `--probe`, `--config`, `--json` |
| `providers huggingface scan` | Scan health of HuggingFace models | `--config`, `--filter/-f`, `--only-up`, `--only-down`, `--json`, `--max-scans`, `--debug` |

`fetch` requires no key for OpenRouter; other providers need their key in the keychain. `scan` requires a provider cache (run `fetch` first). `--probe` verifies availability with a minimal request during fetch. `--debug` writes request/response logs to stdout and a timestamped JSON file.

### `auth` — Manage secure API keys in the system keychain

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `auth set KEY_NAME VALUE` | Store a key; also accepts `KEY=VALUE` as a single argument | `KEY_NAME`, `VALUE` |
| `auth delete KEY_NAME` | Remove a key from the keychain | `KEY_NAME` |
| `auth list` | Show Stored/Missing status for tracked keys | *(none)* |

Tracked keys: `OPENROUTER_API_KEY`, `ARTIFICIAL_ANALYSIS_API_KEY`, `NVIDIA_API_KEY`, `OLLAMA_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN`.

### `litellm` — Manage LiteLLM service configuration

| Command | Description | Arguments / Flags |
| :--- | :--- | :--- |
| `litellm config check` | Validate the LiteLLM config file exists and is parseable YAML | `--config` |
| `litellm cost-map build` | Merge upstream cost map with local overrides, save to service dir | `--config`, `--source-url` |
| `litellm generate config [PROVIDER]` | Generate LiteLLM YAML for one provider, or all with `--all-providers` | `[PROVIDER]` (`nvidia`, `gemini`, `ollama`, `openrouter`, `huggingface`); `--config`, `--output/-o`, `--dry-run`, `--all-providers` |
| `litellm generate fallbacks` | Generate fallbacks YAML from tier tags and provider scans | `--config`, `--output/-o`, `--dry-run`, `--limit` (default `5`) |
| `litellm generate aliases` | Generate `model_group_alias` YAML (tier1/2/3) from tier tags and per-tier provider order | `--config`, `--output/-o`, `--dry-run` |
<| `litellm generate router_settings` | Merge generated fallbacks + aliases into the stub `router_settings` file (stub never overwritten) | `--config`, `--output/-o`, `--dry-run`, `--stub`, `--from-files`, `--limit` (default `5`) |
| `litellm request-restart` | Request a restart of the LiteLLM service by logging a request entry | `--config`, `--reason/-r` |

### `dashboard` — Generate a status dashboard

```bash
model-manager dashboard [--no-open] [--config PATH]
```

Generates the HTML status dashboard. Opens it in a browser by default; `--no-open` only prints the output path.

### `doctor` — Diagnose tool health and environment

```bash
model-manager doctor [--config PATH] [--verbose/-v]
```

Prints a health report covering Python/package versions, config parse, data directory, JSON data files, secrets, provider caches, and LiteLLM config. `--verbose` adds file sizes. Exits non-zero on failure.

## Guided Discovery Workflow

The `models discover` command provides an interactive, three-phase workflow to map providers to performance identities:

1. **Model Identification**: Search the AA dataset to find and assign the correct `aa_slug` for the model's default variant.
2. **Variant Definition**: Optionally define additional functional variants (e.g. "fast", "cheap") and associate them with specific AA slugs.
3. **Provider Mapping**:
   - **Strong Matches**: Automatically identified via AA for variants with slugs.
    - **Suggested Matches**: Fuzzy-matched from local provider caches (OpenRouter, NVIDIA, Ollama, Gemini, HuggingFace). Users can assign these to variants or skip them.

Scores (Intelligence, Coding, Agentic, plus TTFT/TPS where available) are snapshotted into the variant data during this process for fast local analysis. Useful flags: `--provider` to limit the search, `--refresh` to refresh provider caches and AA scores first, `--yolo` to accept all matches non-interactively.

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
