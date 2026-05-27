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
| `aliases resolve` | Resolve a provider ID to scores | `provider_id` |
| `aliases add` | Manually create a model mapping | `provider`, `id`, `model`, `variant`, `family`, `display_name`, `aa_slug`, `--config` |
| `aliases discover` | Suggest mappings for unmapped IDs | `provider`, `ids`, `--config` |
| `aliases audit` | Report mapping coverage | `ids`, `--config` |
| `auth set` | Store an API key in keychain | `key_name`, `value` |
| `auth delete` | Remove an API key from keychain | `key_name` |
| `auth list` | List stored keys | *(none)* |
| `advisor compare` | Compare multiple models side-by-side | `ids`, `--config` |
| `advisor best` | Find best model for a metric | `--metric`, `--config` |
| `advisor gaps` | Report mapping gaps for IDs | `ids`, `--config` |
| `discover-free` | Discover free OpenRouter models | `--probe`, `--config` |
| `discover-nvidia` | Discover NVIDIA available models | `--probe`, `--config` |
| `discover-ollama` | Discover Ollama available models | `--probe`, `--config` |

## Configuration
Detailed configuration options can be found in the [Configuration Guide](docs/config-guide.md).
