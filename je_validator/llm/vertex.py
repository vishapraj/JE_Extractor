"""
Claude on Google Vertex AI (LLM_PROVIDER=vertex). Optional: the vertex line of
requirements-claude.txt. Auth: gcloud auth application-default login.
"""

import os
from typing import List

import anthropic

from je_validator.llm.claude import ClaudeProvider


class VertexProvider(ClaudeProvider):
    def make_client(self, models: List[str]):
        return anthropic.AnthropicVertex(
            project_id=os.environ["VERTEX_PROJECT_ID"], region=os.getenv("VERTEX_REGION", "global")
        )
