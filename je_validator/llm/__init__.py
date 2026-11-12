"""
Model providers, chosen by LLM_PROVIDER. A provider's module is imported only
when it is chosen, so the packages of the others need not be installed.
Add one by subclassing Provider (base.py) in a new module and listing it here.
"""

import importlib

from je_validator.llm.base import Provider

# name -> (module, class, what to install for it)
PROVIDERS = {
    "ollama": ("je_validator.llm.ollama", "OllamaProvider", "requirements.txt"),
    "anthropic": ("je_validator.llm.claude", "AnthropicProvider", "requirements-claude.txt"),
    "vertex": ("je_validator.llm.vertex", "VertexProvider", "requirements-claude.txt (the vertex line)"),
    "bedrock": ("je_validator.llm.bedrock", "BedrockProvider", "requirements-claude.txt (the bedrock line)"),
}


def load_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        raise ValueError(f"unknown LLM_PROVIDER '{name}' - choose one of: {', '.join(PROVIDERS)}")
    module, cls, install = PROVIDERS[name]
    try:
        return getattr(importlib.import_module(module), cls)()
    except ImportError as e:
        raise ValueError(f"LLM_PROVIDER={name} needs '{e.name}', which isn't installed - see {install}") from None
