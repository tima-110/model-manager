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
1. **Set your API Key**:
   ```bash
   export ARTIFICIAL_ANALYSIS_API_KEY="your_key_here"
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

| Command | Description | Key Flags |
| :--- | :--- | :--- |
| `init` | Initialize data directories | `--config` |
| `sync-litellm` | Sync mappings from LiteLLM config | `--config`, `--provider` |
| `scores sync` | Fetch latest rankings from AA | `--config` |
| `aliases resolve` | Resolve a provider ID to scores | `provider_id` |
| `aliases add` | Manually create a model mapping | `--provider`, `--id`, `--model`, `--variant` |
| `aliases discover` | Suggest mappings for unmapped IDs | `--provider`, `--ids` |
| `aliases audit` | Report mapping coverage | `--ids` |

## Configuration
Detailed configuration options can be found in the [Configuration Guide](docs/config-guide.md).
