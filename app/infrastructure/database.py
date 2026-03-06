"""Async database connection management.

Provides engine creation, session factory, and lifecycle hooks
for FastAPI's lifespan. Uses asyncpg driver for PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _normalize_database_url(url: str) -> str:
    """Normalize DB URL to use asyncpg driver.

    Converts 'postgresql://' to 'postgresql+asyncpg://' for driver-agnostic .env files.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def init_database(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create async engine and session factory.

    Returns (engine, session_factory) tuple for use in app lifespan.
    """
    url = _normalize_database_url(database_url)

    # Disable SSL for local connections
    connect_args: dict[str, object] = {}
    if "localhost" in url or "127.0.0.1" in url:
        connect_args["ssl"] = False

    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    return engine, factory


async def close_database(engine: AsyncEngine) -> None:
    """Dispose of the async engine and its connection pool."""
    await engine.dispose()


async def get_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a scoped async session for dependency injection."""
    async with factory() as session:
        yield session
