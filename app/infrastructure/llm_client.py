"""LLM Client — wraps ChatOpenAI as an infrastructure dependency.

The LLM is treated as infrastructure, not architecture.
Swapping OpenAI for Anthropic requires changing only this file.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.infrastructure.settings import Settings


class LLMClient:
    """Thin wrapper around ChatOpenAI for dependency injection."""

    def __init__(self, main_model: ChatOpenAI, repair_model: ChatOpenAI) -> None:
        self._main_model = main_model
        self._repair_model = repair_model

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        """Factory: build LLMClient from application settings."""
        main_model = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            temperature=settings.openai_temperature,
            streaming=True,
            max_retries=settings.llm_client_max_retries,
        )
        repair_model = ChatOpenAI(
            model=settings.openai_repair_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            temperature=settings.openai_repair_temperature,
            streaming=True,
            max_retries=settings.llm_client_max_retries,
        )
        return cls(main_model=main_model, repair_model=repair_model)

    @property
    def model(self) -> ChatOpenAI:
        """Backwards-compatible alias for the main model."""
        return self._main_model

    @property
    def main_model(self) -> ChatOpenAI:
        """Primary model for item estimation."""
        return self._main_model

    @property
    def repair_model(self) -> ChatOpenAI:
        """Cheaper model for repair/correction retries."""
        return self._repair_model
