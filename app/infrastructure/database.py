"""Async database connection management.

Provides engine creation, session factory, and lifecycle hooks
for FastAPI's lifespan across SQLite and PostgreSQL backends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _normalize_database_url(url: str) -> str:
    """Normalize DB URL to use asyncpg when needed.

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

    kwargs = {}
    if not url.startswith("sqlite"):
        kwargs = {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
        }

    engine = create_async_engine(
        url,
        echo=False,
        connect_args=connect_args,
        **kwargs
    )

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    return engine, factory


async def close_database(engine: AsyncEngine) -> None:
    """Dispose of the async engine and its connection pool."""
    await engine.dispose()


async def ensure_runtime_schema(engine: AsyncEngine) -> None:
    """Apply legacy compatibility schema fixes for existing local databases."""

    async with engine.begin() as conn:
        existing_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("item_results")
            }
        )

        if "item_key" not in existing_columns:
            await conn.execute(text("ALTER TABLE item_results ADD COLUMN item_key VARCHAR"))
        if "telemetry_json" not in existing_columns:
            await conn.execute(
                text("ALTER TABLE item_results ADD COLUMN telemetry_json JSON")
            )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_item_results_estimation_item_key "
                "ON item_results (estimation_id, item_key) "
                "WHERE item_key IS NOT NULL"
            )
        )


async def get_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a scoped async session for dependency injection."""
    async with factory() as session:
        yield session
