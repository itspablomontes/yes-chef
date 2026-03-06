"""Application settings via Pydantic BaseSettings.

Reads from .env file and environment variables. Cached via @lru_cache
for singleton behavior across the application.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    database_url: str = "sqlite+aiosqlite:///app/data/yeschef.db"
    openai_api_key: str = ""

    # Optional with defaults
    openai_model: str = "gpt-4o-mini"
    chroma_path: str = "/app/data/chroma"
    batch_size: int = 5
    log_level: str = "INFO"
    app_env: str = "development"
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
