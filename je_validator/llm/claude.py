"""
Claude through the Anthropic API (LLM_PROVIDER=anthropic), and what Claude on
any platform shares (vertex.py, bedrock.py). Optional: requirements-claude.txt.
"""

import os
from typing import Any, Dict, List, Tuple, Type

import anthropic

from je_validator.llm.base import Provider, T

# Claude API models that take server-side refusal fallback (Haiku 5.5 does not).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ClaudeProvider(Provider):
    """Calls Claude with structured outputs; a subclass makes the client for its platform."""
    default_model = "claude-haiku-5-5"
    default_escalation_model = "claude-sonnet-5-5"

    def make_client(self, models: List[str]):
        raise NotImplementedError

    def extra(self, model: str) -> Dict[str, Any]:
        """Platform-specific arguments for the call."""
        return {}

    def prepare(self, models: List[str]) -> None:
        self._client = self.make_client(models)
        self._max_tokens = int(os.getenv("MAX_TOKENS", "16000"))

    def complete(self, model: str, system: str, prompt: str, schema: Type[T]) -> Tuple[T, str]:
        try:
            response = self._client.beta.messages.parse(
                model=model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                **self.extra(model),
            )
        except anthropic.APIError as e:
            raise ValueError(f"Claude API error: {e}") from e
        if response.stop_reason == "refusal":
            raise ValueError(f"{model} declined to review these documents")
        if response.stop_reason == "max_tokens":
            raise ValueError(f"The review by {model} was cut off - raise MAX_TOKENS")
        return response.parsed_output, response.model


class AnthropicProvider(ClaudeProvider):
    def make_client(self, models: List[str]):
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise ValueError("No Claude credentials: set ANTHROPIC_API_KEY in .env, or choose another LLM_PROVIDER.")
        return anthropic.Anthropic()

    def extra(self, model: str) -> Dict[str, Any]:
        if model in FALLBACK_MODELS:
            # If the model declines, the API retries on a fallback model inside the same call.
            return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return {}
