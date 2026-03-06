"""LLM Client — wraps ChatOpenAI as an infrastructure dependency.

The LLM is treated as infrastructure, not architecture.
Swapping OpenAI for Anthropic requires changing only this file.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.infrastructure.settings import Settings


class LLMClient:
    """Thin wrapper around ChatOpenAI for dependency injection."""

    def __init__(self, model: ChatOpenAI) -> None:
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        """Factory: build LLMClient from application settings."""
        model = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            temperature=0.0,
            streaming=True,
            max_retries=5,
        )
        return cls(model=model)

    @property
    def model(self) -> ChatOpenAI:
        """Access the underlying ChatOpenAI model."""
        return self._model
