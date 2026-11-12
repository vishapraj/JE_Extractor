"""
Models run by Ollama on this machine, or on your own server (OLLAMA_HOST). No
key, and nothing leaves the machines you run.
"""

import os
from typing import List, Tuple, Type

import ollama
from pydantic import ValidationError

from je_validator.llm.base import Provider, T


class OllamaProvider(Provider):
    default_model = "qwen2.5:7b"

    def __init__(self):
        self._client = ollama.Client()  # http://127.0.0.1:11434, or OLLAMA_HOST
        self._num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768"))  # Ollama's default context is too small for documents

    def prepare(self, models: List[str]) -> None:
        for model in models:
            try:
                self._client.show(model)
            except ConnectionError:
                raise ValueError("Ollama is not running: install it from ollama.com and start it.") from None
            except ollama.ResponseError:
                raise ValueError(f"Local model {model} is not downloaded: run  ollama pull {model}") from None

    def complete(self, model: str, system: str, prompt: str, schema: Type[T]) -> Tuple[T, str]:
        try:
            response = self._client.chat(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                format=schema.model_json_schema(),  # Ollama constrains the reply to the schema
                options={"temperature": 0, "num_ctx": self._num_ctx},
            )
        except ollama.ResponseError as e:
            raise ValueError(f"Local model error: {e}") from None
        if response.done_reason == "length":
            raise ValueError("The documents are too long for the local model - the reply was cut off (raise OLLAMA_NUM_CTX)")
        try:
            return schema.model_validate_json(response.message.content), model
        except ValidationError:
            raise ValueError("The local model's reply did not match the expected format - try again") from None
