from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.application.schema_validator import validate_quote_schema
from app.domain.value_objects import EstimationStatus
from app.infrastructure.postgres_repositories import (
    PostgresEstimationRepository,
    PostgresItemResultRepository,
)
from tests.helpers import CaptureGraph, build_test_app, run_async, sample_menu_spec, setup_sqlite_app_db


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


def test_challenge_smoke_start_resume_and_validate_quote(tmp_path) -> None:
    db_path = tmp_path / "challenge.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    first_run_graph = CaptureGraph(
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
                                    {
                                        "name": "Salt",
                                        "quantity": "1 tsp",
                                        "unit_cost": 0.05,
                                        "source": "estimated",
                                        "sysco_item_number": None,
                                    }
                                ],
                                "ingredient_cost_per_unit": 0.05,
                                "item_key": "appetizers:0",
                            }
                        ]
                    }
                },
            )
        ]
    )

    app = build_test_app(session_factory, first_run_graph)
    client = TestClient(app)

    start_response = client.post("/estimate", json=sample_menu_spec())
    start_events = _parse_sse_events(start_response.text)

    assert start_response.status_code == 200
    assert [event["event"] for event in start_events] == [
        "estimation_started",
        "item_complete",
        "estimation_metrics",
        "estimation_complete",
    ]

    estimation_id = start_events[0]["estimation_id"]

    async def _verify_partial_state() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)
            job = await estimation_repo.get(estimation_id)
            items = await item_repo.get_by_estimation(estimation_id)

            assert job is not None
            assert job.status == EstimationStatus.IN_PROGRESS
            assert job.items_completed == 1
            assert len(items) == 1

    run_async(_verify_partial_state())

    resume_graph = CaptureGraph(
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
                                    {
                                        "name": "Pepper",
                                        "quantity": "1 tsp",
                                        "unit_cost": 0.05,
                                        "source": "estimated",
                                        "sysco_item_number": None,
                                    }
                                ],
                                "ingredient_cost_per_unit": 0.05,
                                "item_key": "appetizers:1",
                            }
                        ]
                    }
                },
            ),
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {
                                "item_name": "Steak",
                                "category": "main_plates",
                                "ingredients": [
                                    {
                                        "name": "Beef",
                                        "quantity": "8 oz",
                                        "unit_cost": 12.0,
                                        "source": "sysco_catalog",
                                        "sysco_item_number": "999",
                                    }
                                ],
                                "ingredient_cost_per_unit": 12.0,
                                "item_key": "main_plates:0",
                            }
                        ]
                    }
                },
            ),
            (
                "updates",
                {
                    "reduce": {
                        "status": "completed",
                        "quote": {
                            "quote_id": "quote-1",
                            "event": "Test Event",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [
                                {
                                    "item_name": "Duplicate Dish",
                                    "category": "appetizers",
                                    "ingredients": [
                                        {
                                            "name": "Salt",
                                            "quantity": "1 tsp",
                                            "unit_cost": 0.05,
                                            "source": "estimated",
                                            "sysco_item_number": None,
                                        }
                                    ],
                                    "ingredient_cost_per_unit": 0.05,
                                },
                                {
                                    "item_name": "Duplicate Dish",
                                    "category": "appetizers",
                                    "ingredients": [
                                        {
                                            "name": "Pepper",
                                            "quantity": "1 tsp",
                                            "unit_cost": 0.05,
                                            "source": "estimated",
                                            "sysco_item_number": None,
                                        }
                                    ],
                                    "ingredient_cost_per_unit": 0.05,
                                },
                                {
                                    "item_name": "Steak",
                                    "category": "main_plates",
                                    "ingredients": [
                                        {
                                            "name": "Beef",
                                            "quantity": "8 oz",
                                            "unit_cost": 12.0,
                                            "source": "sysco_catalog",
                                            "sysco_item_number": "999",
                                        }
                                    ],
                                    "ingredient_cost_per_unit": 12.0,
                                },
                            ],
                        },
                    }
                },
            ),
        ]
    )

    resume_app = build_test_app(session_factory, resume_graph)
    resume_client = TestClient(resume_app)
    resume_response = resume_client.post(f"/estimate/{estimation_id}/resume")
    resume_events = _parse_sse_events(resume_response.text)

    assert resume_response.status_code == 200
    assert resume_graph.states[0]["completed_items"][0]["item_key"] == "appetizers:0"
    assert [event["event"] for event in resume_events] == [
        "estimation_started",
        "item_complete",
        "item_complete",
        "quote_complete",
        "estimation_metrics",
        "estimation_complete",
    ]

    final_quote = resume_events[3]["data"]
    validate_quote_schema(final_quote)
    assert resume_events[5]["data"]["status"] == "completed"

    async def _verify_completed_state() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)
            job = await estimation_repo.get(estimation_id)
            items = await item_repo.get_by_estimation(estimation_id)

            assert job is not None
            assert job.status == EstimationStatus.COMPLETED
            assert job.items_completed == 3
            assert len(items) == 3
            assert job.quote_json == final_quote

        await engine.dispose()

    run_async(_verify_completed_state())
