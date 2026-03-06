"""FastAPI dependency injection factories.

Provides singletons and scoped dependencies for routes.
Uses FastAPI's Depends() system for clean DI.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.estimation_service import EstimationService
from app.infrastructure.postgres_repositories import (
    PostgresEstimationRepository,
    PostgresItemResultRepository,
)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a scoped database session from the app's session factory."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def get_estimation_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> EstimationService:
    """Build an EstimationService with all dependencies wired."""
    graph = request.app.state.compiled_graph

    estimation_repo = PostgresEstimationRepository(session)
    item_result_repo = PostgresItemResultRepository(session)

    return EstimationService(
        graph=graph,
        estimation_repo=estimation_repo,
        item_result_repo=item_result_repo,
    )


EstimationServiceDep = Annotated[EstimationService, Depends(get_estimation_service)]
