"""FastAPI application factory with lifespan management.

Initializes all infrastructure at startup (DB, graph, catalog)
and tears down on shutdown. The app is a singleton container
that holds shared state via app.state.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.graph_builder import GraphBuilder
from app.infrastructure.catalog_index import build_catalog_index
from app.infrastructure.database import close_database, ensure_runtime_schema, init_database
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.settings import get_settings
from app.presentation.routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting Yes Chef API (env: %s)", settings.app_env)

    # 1. Initialize database
    engine, session_factory = init_database(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # 2. Create tables if they don't exist (dev convenience)
    from app.infrastructure.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_runtime_schema(engine)
    logger.info("Database tables ensured")

    # 3. Build catalog index
    catalog_index = build_catalog_index()
    app.state.catalog_index = catalog_index
    logger.info("Catalog loaded: %d entries", catalog_index.size)

    # 4. Build LLM client and graph
    llm_client = LLMClient.from_settings(settings)
    builder = GraphBuilder(llm=llm_client.model)
    graph = builder.build()
    compiled_graph = graph.compile()
    app.state.compiled_graph = compiled_graph
    logger.info("Graph compiled successfully")

    yield  # App is running

    # Shutdown
    logger.info("Shutting down Yes Chef API")
    await close_database(engine)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Yes Chef — Catering Estimation API",
        description="AI agent that decomposes catering menus and produces per-unit ingredient cost quotes.",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(router)

    return app


# Entry point for `uvicorn app.main:app`
app = create_app()
