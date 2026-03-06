from __future__ import annotations

from datetime import datetime

from langchain_core.messages import AIMessageChunk

from app.application.estimation_orchestrator import EstimationOrchestrator
from app.application.estimation_service import EstimationService
from app.application.progress_observer import ProgressObserver
from app.domain.entities import EstimationJob, ItemResult
from app.domain.value_objects import EstimationStatus, IngredientCost, IngredientSource
from app.infrastructure.catalog_index import normalize_query
from app.infrastructure.postgres_repositories import (
    PostgresEstimationRepository,
    PostgresItemResultRepository,
)
from tests.helpers import CaptureGraph, collect_events, run_async, sample_menu_spec, setup_sqlite_app_db


def _menu_spec_with_wagyu_twice() -> dict:
    """Menu with Wagyu Beef in two dishes to test carry-forward on resume."""
    return {
        "event": "Wagyu Test",
        "date": "2026-03-06",
        "venue": "Test",
        "guest_count_estimate": 10,
        "notes": "",
        "categories": {
            "appetizers": [
                {"name": "Wagyu Carpaccio", "description": "Wagyu beef carpaccio", "dietary_notes": None},
                {"name": "Wagyu Sliders", "description": "Mini wagyu beef sliders", "dietary_notes": None},
            ],
            "main_plates": [],
            "desserts": [],
            "cocktails": [],
        },
    }


def test_resume_carry_forward_knowledge_store_short_circuits_lookup(tmp_path) -> None:
    """On resume, knowledge_store from first run is passed to graph; resolver skips re-lookup.

    First run completes one item with 'Wagyu beef' -> not_available. On resume, the
    reconstructed knowledge_store contains that. The graph receives it; the resolver
    would use it to skip catalog search (verified in unit tests).
    """
    db_path = tmp_path / "carry_forward.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    first_run_graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {
                                "item_name": "Wagyu Carpaccio",
                                "category": "appetizers",
                                "item_key": "appetizers:0",
                                "ingredients": [
                                    {
                                        "name": "Wagyu beef",
                                        "quantity": "2 oz",
                                        "unit_cost": None,
                                        "source": "not_available",
                                        "sysco_item_number": None,
                                    },
                                ],
                                "ingredient_cost_per_unit": 0.0,
                            }
                        ]
                    }
                },
            )
        ]
    )

    async def _run() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)

            service = EstimationService(
                graph=first_run_graph,
                estimation_repo=estimation_repo,
                item_result_repo=item_repo,
            )
            events = await collect_events(
                service.create_estimation(_menu_spec_with_wagyu_twice())
            )

            estimation_id = events[0]["estimation_id"]
            assert len(first_run_graph.states) >= 1

            resume_graph = CaptureGraph(
                [
                    (
                        "updates",
                        {
                            "item_worker": {
                                "completed_items": [
                                    {
                                        "item_name": "Wagyu Sliders",
                                        "category": "appetizers",
                                        "item_key": "appetizers:1",
                                        "ingredients": [
                                            {
                                                "name": "Wagyu beef",
                                                "quantity": "2 oz",
                                                "unit_cost": None,
                                                "source": "not_available",
                                                "sysco_item_number": None,
                                            },
                                        ],
                                        "ingredient_cost_per_unit": 0.0,
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
                                    "quote_id": "q1",
                                    "event": "Wagyu Test",
                                    "generated_at": "2026-03-06T00:00:00Z",
                                    "line_items": [],
                                },
                            }
                        },
                    ),
                ]
            )

            resumed_events = await collect_events(
                EstimationService(
                    graph=resume_graph,
                    estimation_repo=estimation_repo,
                    item_result_repo=item_repo,
                ).resume_estimation(estimation_id)
            )

            assert len(resume_graph.states) >= 1
            knowledge = resume_graph.states[0].get("knowledge_store", {})
            wagyu_key = normalize_query("Wagyu beef")
            assert wagyu_key in knowledge, f"Expected {wagyu_key} in knowledge_store: {knowledge}"
            assert knowledge[wagyu_key] == "not_available"

        await engine.dispose()

    run_async(_run())


def test_resume_rebuilds_price_cache_from_completed_items(tmp_path) -> None:
    """On resume, memo_store includes price_cache rebuilt from completed items."""
    db_path = tmp_path / "price_cache.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    first_run_graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {
                                "item_name": "Butter Dish",
                                "category": "appetizers",
                                "item_key": "appetizers:0",
                                "ingredients": [
                                    {
                                        "name": "Butter",
                                        "quantity": "0.5 tbsp",
                                        "unit_cost": 0.42,
                                        "source": "sysco_catalog",
                                        "sysco_item_number": "12345",
                                    },
                                    {
                                        "name": "Salt",
                                        "quantity": "1 tsp",
                                        "unit_cost": 0.05,
                                        "source": "estimated",
                                        "sysco_item_number": None,
                                    },
                                ],
                                "ingredient_cost_per_unit": 0.47,
                            }
                        ]
                    }
                },
            )
        ]
    )

    menu_spec = {
        "event": "Butter Test",
        "categories": {
            "appetizers": [
                {"name": "Butter Dish", "description": "Butter and salt"},
                {"name": "Another Butter Dish", "description": "Same butter"},
            ],
            "main_plates": [],
            "desserts": [],
            "cocktails": [],
        },
    }

    async def _run() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)

            service = EstimationService(
                graph=first_run_graph,
                estimation_repo=estimation_repo,
                item_result_repo=item_repo,
            )
            events = await collect_events(service.create_estimation(menu_spec))
            estimation_id = events[0]["estimation_id"]

            resume_graph = CaptureGraph(
                [
                    (
                        "updates",
                        {
                            "item_worker": {
                                "completed_items": [
                                    {
                                        "item_name": "Another Butter Dish",
                                        "category": "appetizers",
                                        "item_key": "appetizers:1",
                                        "ingredients": [],
                                        "ingredient_cost_per_unit": 0.0,
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
                                "quote": {"quote_id": "q1", "generated_at": "2026-03-06T00:00:00Z", "line_items": []},
                            }
                        },
                    ),
                ]
            )

            await collect_events(
                EstimationService(
                    graph=resume_graph,
                    estimation_repo=estimation_repo,
                    item_result_repo=item_repo,
                ).resume_estimation(estimation_id)
            )

            assert len(resume_graph.states) >= 1
            memo = resume_graph.states[0].get("memo_store", {})
            price_cache = memo.get("price_cache", {})
            expected_key = "12345::0.5 tbsp"
            assert expected_key in price_cache, f"Expected {expected_key} in price_cache: {price_cache}"
            assert price_cache[expected_key].get("unit_cost") == 0.42

        await engine.dispose()

    run_async(_run())


def test_estimation_service_create_and_resume_align_small_work_units(tmp_path) -> None:
    db_path = tmp_path / "service.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    async def _run() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)

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
                                            {
                                                "name": "Sea salt",
                                                "quantity": "1 tsp",
                                                "unit_cost": 0.05,
                                                "source": "estimated",
                                                "sysco_item_number": None,
                                            },
                                            {
                                                "name": "Butter",
                                                "quantity": "0.5 tbsp",
                                                "unit_cost": 0.04,
                                                "source": "sysco_catalog",
                                                "sysco_item_number": "123",
                                            },
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
                                    "quote_id": "q1",
                                    "event": "Test Event",
                                    "generated_at": "2026-03-06T00:00:00Z",
                                    "line_items": [],
                                }
                            }
                        },
                    ),
                ]
            )

            service = EstimationService(graph=graph, estimation_repo=estimation_repo, item_result_repo=item_repo)
            events = await collect_events(service.create_estimation(sample_menu_spec()))

            assert [event["event"] for event in events] == [
                "estimation_started",
                "item_complete",
                "quote_complete",
                "estimation_metrics",
                "estimation_complete",
            ]
            assert graph.states[0]["completed_items"] == []

            jobs = await estimation_repo.get(events[0]["estimation_id"])
            assert jobs is not None
            estimation_id = jobs.id

            resumed_graph = CaptureGraph([])
            resumed_service = EstimationService(
                graph=resumed_graph,
                estimation_repo=estimation_repo,
                item_result_repo=item_repo,
            )
            await collect_events(resumed_service.resume_estimation(estimation_id))

            resumed_completed = resumed_graph.states[0]["completed_items"]
            assert resumed_completed[0]["item_key"] == "appetizers:0"
            assert resumed_graph.states[0]["knowledge_store"][normalize_query("sea salt")] == "estimated"
            assert resumed_graph.states[0]["knowledge_store"][normalize_query("butter")] == "found:123"

        await engine.dispose()

    run_async(_run())


def test_orchestrator_uses_item_key_to_avoid_duplicate_notifications() -> None:
    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "item_worker": {
                        "completed_items": [
                            {"item_name": "Duplicate Dish", "category": "appetizers", "ingredient_cost_per_unit": 1.0, "ingredients": [], "item_key": "appetizers:0"},
                            {"item_name": "Duplicate Dish", "category": "appetizers", "ingredient_cost_per_unit": 1.2, "ingredients": [], "item_key": "appetizers:1"},
                        ]
                    }
                },
            ),
            (
                "updates",
                {
                    "reduce": {
                        "quote": {
                            "quote_id": "q1",
                            "event": "Test Event",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [],
                        }
                    }
                },
            ),
        ]
    )

    class Observer:
        def __init__(self) -> None:
            self.items: list[str] = []
            self.completed = False

        async def on_item_complete(self, estimation_id: str, item_data):
            self.items.append(item_data["item_key"])

        async def on_estimation_complete(self, estimation_id: str, quote):
            self.completed = True

        async def on_error(self, estimation_id: str, error: str):
            raise AssertionError(error)

    async def _run() -> None:
        orchestrator = EstimationOrchestrator(graph)
        observer = Observer()
        orchestrator.add_observer(observer)
        events = await collect_events(
            orchestrator.stream(
                "est-1",
                {
                    "estimation_id": "est-1",
                    "menu_spec": sample_menu_spec(),
                    "completed_items": [],
                    "knowledge_store": {},
                    "status": "in_progress",
                },
            )
        )

        assert observer.items == ["appetizers:0", "appetizers:1"]
        assert observer.completed is True
        assert [event["event"] for event in events] == [
            "estimation_started",
            "item_complete",
            "item_complete",
            "quote_complete",
            "estimation_metrics",
            "estimation_complete",
        ]

    run_async(_run())


def test_orchestrator_does_not_emit_legacy_tool_call_events_from_message_chunks() -> None:
    graph = CaptureGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "search_catalog",
                                "id": "call-1",
                                "args": "",
                                "index": 0,
                            }
                        ],
                    ),
                    {},
                ),
            ),
            (
                "updates",
                {
                    "reduce": {
                        "status": "completed",
                        "quote": {
                            "quote_id": "q1",
                            "event": "Test Event",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [],
                        },
                    }
                },
            ),
        ]
    )

    async def _run() -> None:
        orchestrator = EstimationOrchestrator(graph)
        events = await collect_events(
            orchestrator.stream(
                "est-1",
                {
                    "estimation_id": "est-1",
                    "menu_spec": sample_menu_spec(),
                    "completed_items": [],
                    "knowledge_store": {},
                    "status": "in_progress",
                },
            )
        )

        assert [event["event"] for event in events] == [
            "estimation_started",
            "quote_complete",
            "estimation_metrics",
            "estimation_complete",
        ]

    run_async(_run())


def test_orchestrator_emits_error_for_invalid_quote_schema() -> None:
    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "reduce": {
                        "quote": {
                            "quote_id": "q1",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [],
                        }
                    }
                },
            )
        ]
    )

    class Observer:
        def __init__(self) -> None:
            self.error: str | None = None

        async def on_item_complete(self, estimation_id: str, item_data):
            raise AssertionError("No items should be persisted for an invalid final quote")

        async def on_estimation_complete(self, estimation_id: str, quote):
            raise AssertionError("Invalid quote should not be persisted as completed")

        async def on_error(self, estimation_id: str, error: str):
            self.error = error

    async def _run() -> None:
        orchestrator = EstimationOrchestrator(graph)
        observer = Observer()
        orchestrator.add_observer(observer)
        events = await collect_events(
            orchestrator.stream(
                "est-1",
                {
                    "estimation_id": "est-1",
                    "menu_spec": sample_menu_spec(),
                    "completed_items": [],
                    "knowledge_store": {},
                    "status": "in_progress",
                },
            )
        )

        assert [event["event"] for event in events] == [
            "estimation_started",
            "error",
        ]
        assert observer.error is not None
        assert "Quote schema validation failed" in observer.error

    run_async(_run())


def test_orchestrator_uses_reduce_status_in_estimation_complete() -> None:
    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "reduce": {
                        "status": "completed_with_failures",
                        "quote": {
                            "quote_id": "q1",
                            "event": "Test Event",
                            "generated_at": "2026-03-06T00:00:00Z",
                            "line_items": [],
                        },
                    }
                },
            )
        ]
    )

    async def _run() -> None:
        orchestrator = EstimationOrchestrator(graph)
        events = await collect_events(
            orchestrator.stream(
                "est-1",
                {
                    "estimation_id": "est-1",
                    "menu_spec": sample_menu_spec(),
                    "completed_items": [],
                    "knowledge_store": {},
                    "status": "in_progress",
                },
            )
        )

        assert events[-1] == {
            "event": "estimation_complete",
            "data": {
                "status": "completed_with_failures",
                "items_processed": 0,
            },
        }

    run_async(_run())


def test_orchestrator_updates_final_status_without_quote_event() -> None:
    graph = CaptureGraph(
        [
            (
                "updates",
                {
                    "reduce": {
                        "status": "completed",
                    }
                },
            )
        ]
    )

    async def _run() -> None:
        orchestrator = EstimationOrchestrator(graph)
        events = await collect_events(
            orchestrator.stream(
                "est-1",
                {
                    "estimation_id": "est-1",
                    "menu_spec": sample_menu_spec(),
                    "completed_items": [],
                    "knowledge_store": {},
                    "status": "in_progress",
                },
            )
        )

        assert [event["event"] for event in events] == [
            "estimation_started",
            "estimation_metrics",
            "estimation_complete",
        ]
        assert events[-1]["data"]["status"] == "completed"

    run_async(_run())


def test_repository_persistence_round_trip(tmp_path) -> None:
    db_path = tmp_path / "repos.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    async def _run() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)

            job = EstimationJob(
                id="job-1",
                event_name="Repo Test",
                total_items=2,
                items_completed=0,
                status=EstimationStatus.PENDING,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                menu_spec_json=sample_menu_spec(),
            )
            await estimation_repo.create(job)
            await estimation_repo.update_status("job-1", EstimationStatus.IN_PROGRESS)
            await estimation_repo.update_progress("job-1", 1)
            await estimation_repo.update_quote("job-1", {"quote_id": "q1"})

            item = ItemResult(
                id="item-1",
                estimation_id="job-1",
                item_name="Duplicate Dish",
                category="appetizers",
                ingredients=[
                    IngredientCost("Sea salt", "1 tsp", 0.05, IngredientSource.ESTIMATED),
                    IngredientCost("Butter", "0.5 tbsp", 0.04, IngredientSource.SYSCO_CATALOG, "123"),
                ],
                ingredient_cost_per_unit=0.09,
            )
            await item_repo.save(item)

            stored_job = await estimation_repo.get("job-1")
            stored_items = await item_repo.get_by_estimation("job-1")

            assert stored_job is not None
            assert stored_job.status == EstimationStatus.IN_PROGRESS
            assert stored_job.items_completed == 1
            assert stored_job.quote_json == {"quote_id": "q1"}
            assert len(stored_items) == 1
            assert stored_items[0].ingredients[1].sysco_item_number == "123"

        await engine.dispose()

    run_async(_run())


def test_progress_observer_updates_progress_and_quote(tmp_path) -> None:
    db_path = tmp_path / "observer.sqlite"
    engine, session_factory = setup_sqlite_app_db(db_path)

    async def _run() -> None:
        async with session_factory() as session:
            estimation_repo = PostgresEstimationRepository(session)
            item_repo = PostgresItemResultRepository(session)
            observer = ProgressObserver(estimation_repo, item_repo)

            await estimation_repo.create(
                EstimationJob(
                    id="job-2",
                    event_name="Observer Test",
                    total_items=1,
                    items_completed=0,
                    status=EstimationStatus.PENDING,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    menu_spec_json=sample_menu_spec(),
                )
            )

            await observer.on_item_complete(
                "job-2",
                {
                    "item_name": "Steak",
                    "category": "main_plates",
                    "item_key": "main_plates:0",
                    "ingredients": [
                        {"name": "Beef", "quantity": "8 oz", "unit_cost": 12.0, "source": "sysco_catalog", "sysco_item_number": "999"},
                        {"name": "Salt", "quantity": "1 tsp", "unit_cost": 0.05, "source": "estimated", "sysco_item_number": None},
                    ],
                    "ingredient_cost_per_unit": 12.05,
                },
            )
            await observer.on_estimation_complete(
                "job-2",
                {
                    "quote_id": "q2",
                    "event": "Observer Test",
                    "generated_at": "2026-03-06T00:00:00Z",
                    "line_items": [],
                },
            )

            stored_job = await estimation_repo.get("job-2")
            stored_items = await item_repo.get_by_estimation("job-2")

            assert stored_job is not None
            assert stored_job.items_completed == 1
            assert stored_job.status == EstimationStatus.COMPLETED
            assert stored_job.quote_json["quote_id"] == "q2"
            assert len(stored_items) == 1

        await engine.dispose()

    run_async(_run())
