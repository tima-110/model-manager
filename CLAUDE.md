# model-manager

A professional CLI tool for managing model performance rankings and identifier aliases for LiteLLM installations.

## Project Structure
Follows the standard CLI src-layout:
- `src/model_manager/`: Core package logic.
- `data/`: Local cache (should be ignored in git; actual state lives in `platformdirs`).
- `tests/`: Pytest suite.
- `docs/`: Configuration and user guides.

## Installation & Dev Workflow
- **Installation**: `pipx install -e .`
- **Execution**: Use the `model-manager` command globally.
- **Testing**: `python3 -m pytest` or via a venv.
- **Configuration**: Managed via `config.toml` in the platform-specific config directory.

## Key Architectural Decisions
- **Variant-Based Aliasing**: Uses a hierarchy of `Model` $\rightarrow$ `Variant` $\rightarrow$ `Provider ID` to handle model variants (e.g., quantized vs. full) and their corresponding Artificial Analysis (AA) scores.
- **Standardized CLI**: Built with `Typer`, `Rich`, and `Pydantic` to ensure a consistent and robust user experience.
- **Automated Discovery**: Integrates with `/etc/litellm/litellm.yaml` to automatically identify and suggest mappings for used models.
- **State Management**: Uses `platformdirs` to store scores and aliases in the appropriate user data directories.
