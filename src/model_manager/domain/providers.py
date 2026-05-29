"""Domain logic for managing supported providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

@dataclass
class Provider:
    """Represents a supported model provider."""
    name: str
    secret_key: str

# The current list of supported providers.
# This will eventually evolve to support dynamic/generic providers.
SUPPORTED_PROVIDERS = [
    Provider(name="OpenRouter", secret_key="OPENROUTER_API_KEY"),
    Provider(name="NVIDIA", secret_key="NVIDIA_API_KEY"),
    Provider(name="Ollama", secret_key="OLLAMA_API_KEY"),
]

def list_providers() -> List[Provider]:
    """Return the list of supported providers."""
    return SUPPORTED_PROVIDERS
