"""Application settings via Pydantic BaseSettings.

Reads from .env file and environment variables. Cached via @lru_cache
for singleton behavior across the application.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Required
    database_url: str = "sqlite+aiosqlite:////app/data/yeschef.db"
    openai_api_key: str = ""

    # Optional with defaults
    openai_model: str = "gpt-4o-mini"
    openai_repair_model: str = "gpt-5-nano"
    openai_temperature: float = 0.0
    openai_repair_temperature: float = 0.0
    llm_client_max_retries: int = 2
    llm_rate_limit_attempts: int = 4
    batch_size: int = 5
    planning_pool_size: int = 6
    tool_result_max_matches: int = 3
    log_level: str = "INFO"
    app_env: str = "development"
    debug: bool = False

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
