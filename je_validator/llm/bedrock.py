"""
Claude on Amazon Bedrock (LLM_PROVIDER=bedrock). Optional: the bedrock line of
requirements-claude.txt. Auth: your AWS profile or credentials.
"""

import os
from typing import Any, Dict, List, Optional

import anthropic

from je_validator.llm.claude import ClaudeProvider


class BedrockProvider(ClaudeProvider):
    default_model = "anthropic.claude-haiku-5-5"
    default_escalation_model = "anthropic.claude-sonnet-5-5"

    def escalation_default(self, model: str) -> Optional[str]:
        # An ARN (e.g. an inference profile) names one model your account was given, so there is
        # no default second one: set LLM_ESCALATION_MODEL to a second ARN, or low confidence
        # goes straight to human review.
        return None if model.startswith("arn:") else self.default_escalation_model

    def make_client(self, models: List[str]):
        if models[0].startswith("arn:"):
            # ARNs are served by Bedrock's InvokeModel endpoint, in the ARN's own region
            # (arn:aws:bedrock:<region>:<account>:...).
            return anthropic.AnthropicBedrock(aws_region=os.getenv("AWS_REGION") or models[0].split(":")[3])
        return anthropic.AnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"])

    def extra(self, model: str) -> Dict[str, Any]:
        if model.startswith("arn:"):
            # Bedrock accepts the SDK's default API version (2026-10-21) only for Claude 4.7 and
            # later; the older Bedrock version works for every model, e.g. Sonnet 4.5 profiles.
            return {"extra_body": {"anthropic_version": "bedrock-2023-05-31"}}
        return {}
