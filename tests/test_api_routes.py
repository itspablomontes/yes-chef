from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.entities import EstimationJob, ItemResult
from app.domain.value_objects import EstimationStatus, IngredientCost, IngredientSource
from app.infrastructure.postgres_repositories import (
    PostgresEstimationRepository,
    PostgresItemResultRepository,
)
from tests.helpers import build_test_app, run_async, sample_menu_spec, setup_sqlite_app_db, CaptureGraph


def _parse_sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for chunk in text.split("\n\n"):
        if "data: " not in chunk:
            continue
        data_line = next(
            (line for line in chunk.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            continue
        events.append(json.loads(data_line.replace("data: ", "", 1)))
    return events


def test_post_estimate_streams_events_and_persists_state(tmp_path) -> None:
    db_path = tmp_path / "api.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {
                                "item_name": "Duplicate Dish",
                                "category": "appetizers",
                                "ingredients": [
                                    {"name": "Salt", "quantity": "1 tsp", "unit_cost": 0.05, "source": "estimated", "sysco_item_number": None},
                                    {"name": "Butter", "quantity": "0.5 tbsp", "unit_cost": 0.04, "source": "sysco_catalog", "sysco_item_number": "123"},
                                ],
                                "ingredient_cost_per_unit": 0.09,
                                "item_key": "appetizers:0",
                            }
                        ]
                    }
                },
            ),
            (
                "updates",
                {
                    "reduce": {
                        "quote": {
                            "quote_id": "quote-1",
                            "event": "Test Event",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [],
                        }
                    }
                },
            ),
        ]
    )

    app = build_test_app(session_factory, graph)
    client = TestClient(app)
    response = client.post("/estimate", json=sample_menu_spec())
    events = _parse_sse_events(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert [event["event"] for event in events] == [
        "estimation_started",
        "item_complete",
        "quote_complete",
        "estimation_metrics",
        "estimation_complete",
    ]

    estimation_id = events[0]["estimation_id"]

    async def _verify() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)
            job = await estimation_repo.get(estimation_id)
            items = await item_repo.get_by_estimation(estimation_id)

            assert job is not None
            assert job.items_completed == 1
            assert job.status == EstimationStatus.COMPLETED
            assert len(items) == 1

        await engine.dispose()

    run_async(_verify())


def test_get_status_and_resume_only_stream_remaining_items(tmp_path) -> None:
    db_path = tmp_path / "resume.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    async def _seed() -> str:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)

            await estimation_repo.create(
                EstimationJob(
                    id="resume-job",
                    event_name="Test Event",
                    total_items=3,
                    items_completed=1,
                    status=EstimationStatus.IN_PROGRESS,
                    created_at=__import__("datetime").datetime.now(),
                    updated_at=__import__("datetime").datetime.now(),
                    menu_spec_json=sample_menu_spec(),
                )
            )
            await item_repo.save(
                ItemResult(
                    id="item-1",
                    estimation_id="resume-job",
                    item_name="Duplicate Dish",
                    category="appetizers",
                    ingredients=[
                        IngredientCost("Salt", "1 tsp", 0.05, IngredientSource.ESTIMATED),
                        IngredientCost("Butter", "0.5 tbsp", 0.04, IngredientSource.SYSCO_CATALOG, "123"),
                    ],
                    ingredient_cost_per_unit=0.09,
                )
            )
            return "resume-job"

    estimation_id = run_async(_seed())

    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {
                                "item_name": "Duplicate Dish",
                                "category": "appetizers",
                                "ingredients": [
                                    {"name": "Pepper", "quantity": "1 tsp", "unit_cost": 0.05, "source": "estimated", "sysco_item_number": None},
                                    {"name": "Oil", "quantity": "1 tbsp", "unit_cost": 0.07, "source": "estimated", "sysco_item_number": None},
                                ],
                                "ingredient_cost_per_unit": 0.12,
                                "item_key": "appetizers:1",
                            }
                        ]
                    }
                },
            )
        ]
    )
    app = build_test_app(session_factory, graph)
    client = TestClient(app)

    status_response = client.get(f"/estimate/{estimation_id}")
    assert status_response.status_code == 200
    assert status_response.json()["items_completed"] == 1

    resume_response = client.post(f"/estimate/{estimation_id}/resume")
    events = _parse_sse_events(resume_response.text)

    assert resume_response.status_code == 200
    assert graph.states[0]["completed_items"][0]["item_key"] == "appetizers:0"
    assert events[1]["data"]["item_key"] == "appetizers:1"

    run_async(engine.dispose())
