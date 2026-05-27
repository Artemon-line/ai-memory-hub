from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


class InferenceProvider(Protocol):
    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Get a completion from the LLM."""
        ...


class OpenAIInferenceProvider:
    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com",
        api_key: str | None = None,
    ):
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key and "api.openai.com" in base_url:
            logger.warning("OPENAI_API_KEY is missing for OpenAI inference")

        self.client = OpenAI(base_url=base_url, api_key=key or "dummy")
        self.model = model

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages, # type: ignore
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return content if content is not None else ""


class LocalInferenceProvider:
    """Deterministic local inference provider for testing."""

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        # Simple mock response that returns a minimal valid conversation
        return (
            '{"messages": [{"role": "user", "text": "mocked text"}], '
            '"metadata": {"topics": ["mock"]}}'
        )
