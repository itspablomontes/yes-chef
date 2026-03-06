from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.infrastructure.database import init_database
from app.infrastructure.models import Base
from app.presentation.routes import router


def run_async(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


async def collect_events(
    generator: AsyncGenerator[dict[str, Any], None],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class CaptureGraph:
    def __init__(self, outputs: list[tuple[str, Any]]) -> None:
        self.outputs = outputs
        self.states: list[dict[str, Any]] = []

    async def astream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]):
        self.states.append(state)
        for mode, output in self.outputs:
            yield mode, output


def build_test_app(session_factory: Any, graph: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = session_factory
    app.state.compiled_graph = graph
    return app


def setup_sqlite_app_db(db_path: Path):
    engine, session_factory = init_database(f"sqlite+aiosqlite:///{db_path}")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    run_async(_init())
    return engine, session_factory


def sample_menu_spec() -> dict[str, Any]:
    return {
        "event": "Test Event",
        "date": "2026-03-06",
        "venue": "Test Venue",
        "guest_count_estimate": 10,
        "notes": "",
        "categories": {
            "appetizers": [
                {
                    "name": "Duplicate Dish",
                    "description": "First duplicate",
                    "dietary_notes": None,
                    "service_style": "passed",
                },
                {
                    "name": "Duplicate Dish",
                    "description": "Second duplicate",
                    "dietary_notes": None,
                    "service_style": "passed",
                },
            ],
            "main_plates": [
                {
                    "name": "Steak",
                    "description": "A steak plate",
                    "dietary_notes": "GF",
                }
            ],
        },
    }
