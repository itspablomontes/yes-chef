from __future__ import annotations

from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agent.contracts.item_pipeline import IngredientPlanPayload, PlannedIngredient
from app.agent.nodes.batch_worker import ItemWorkerNode
from app.agent.nodes.ingredient_planner import IngredientPlannerNode
from app.agent.nodes.planning_pool import PlanningPool
from app.agent.state import EstimationState
from app.application.stream_events import bind_progress_event_sink
from app.infrastructure.catalog_index import normalize_query
from tests.helpers import collect_events, run_async, sample_menu_spec


class FakeLLM:
    def __init__(self, *, usage_in_metadata_only: bool = False) -> None:
        self._usage_in_metadata_only = usage_in_metadata_only

    def bind_tools(self, tools: list[Any]) -> "FakeLLM":
        return self

    def with_structured_output(
        self, schema: type, *, include_raw: bool = False
    ) -> "FakeStructuredLLM":
        return FakeStructuredLLM(
            schema, include_raw=include_raw, usage_in_metadata_only=self._usage_in_metadata_only
        )


class FakeStructuredLLM:
    def __init__(
        self,
        schema: type,
        *,
        include_raw: bool = False,
        usage_in_metadata_only: bool = False,
    ) -> None:
        self._schema = schema
        self._include_raw = include_raw
        self._usage_in_metadata_only = usage_in_metadata_only

    async def ainvoke(self, messages: list[Any]) -> Any:
        if self._schema == IngredientPlanPayload:
            parsed = IngredientPlanPayload(
                ingredients=[
                    PlannedIngredient(name="bacon", quantity_needed="1 strip", needs_catalog_lookup=True),
                    PlannedIngredient(name="scallops", quantity_needed="2 each", needs_catalog_lookup=True),
                ]
            )
            if self._include_raw:
                if self._usage_in_metadata_only:
                    raw = AIMessage(
                        content="",
                        response_metadata={"model_provider": "openai"},
                        usage_metadata={
                            "input_tokens": 15,
                            "output_tokens": 25,
                            "total_tokens": 40,
                        },
                    )
                else:
                    raw = AIMessage(
                        content="",
                        response_metadata={"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
                    )
                return {
                    "raw": raw,
                    "parsed": parsed,
                    "parsing_error": None,
                }
            return parsed
        raise ValueError(f"Unknown schema: {self._schema}")


def _make_estimation_state_for_item(item_key: str) -> Any:
    """Build state with menu spec containing one item at the given key."""
    cat, idx = item_key.split(":") if ":" in item_key else ("appetizers", "0")
    menu_spec = {
        "event": "Test",
        "categories": {
            cat: [
                {
                    "name": "Duplicate Dish",
                    "description": "Test item",
                    "category": cat,
                    "dietary_notes": None,
                    "service_style": "passed",
                }
            ]
        },
    }
    return EstimationState(
        estimation_id="est-1",
        menu_spec=menu_spec,
        completed_items=[],
        knowledge_store={},
        status="in_progress",
    )


def test_planning_pool_runs_n_items_concurrently() -> None:
    fake_llm = FakeLLM()
    planner = IngredientPlannerNode(llm=fake_llm)
    pool = PlanningPool(planner=planner, max_concurrency=4)
    items = [
        {"name": f"Item {i}", "description": f"Desc {i}", "category": "appetizers", "item_key": f"appetizers:{i}"}
        for i in range(8)
    ]

    async def _run() -> None:
        results = await pool.plan_batch(items, knowledge={})
        assert len(results) == 8
        for r in results:
            assert "usage" in r
            assert r["usage"]["total_tokens"] == 30

    run_async(_run())


def test_ingredient_planner_returns_structured_plan() -> None:
    fake_llm = FakeLLM()
    node = IngredientPlannerNode(llm=fake_llm)
    state = _make_estimation_state_for_item("appetizers:0")

    async def _run() -> None:
        update = await node(state)
        assert "planned_ingredients" in update
        assert len(update["planned_ingredients"]) > 0
        assert "usage" in update
        assert update["usage"]["prompt_tokens"] == 10
        assert update["usage"]["completion_tokens"] == 20
        assert update["usage"]["total_tokens"] == 30

    run_async(_run())


def test_ingredient_planner_extracts_usage_from_usage_metadata() -> None:
    """Usage is extracted from usage_metadata when response_metadata has no usage."""
    fake_llm = FakeLLM(usage_in_metadata_only=True)
    node = IngredientPlannerNode(llm=fake_llm)
    state = _make_estimation_state_for_item("appetizers:0")

    async def _run() -> None:
        update = await node(state)
        assert "usage" in update
        assert update["usage"]["prompt_tokens"] == 15
        assert update["usage"]["completion_tokens"] == 25
        assert update["usage"]["total_tokens"] == 40

    run_async(_run())


def test_item_worker_processes_one_item_and_updates_knowledge() -> None:
    """ItemWorkerNode uses FakeLLM which returns bacon+scallops; knowledge_store gets normalized keys."""
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]

    async def _run() -> None:
        result = await node(
            EstimationState(
                estimation_id="est-1",
                menu_spec=sample_menu_spec(),
                completed_items=[],
                knowledge_store={},
                status="in_progress",
            )
        )

        completed = result["completed_items"]
        assert len(completed) >= 1
        assert completed[0]["item_name"] == "Duplicate Dish"
        assert completed[0]["item_key"] == "appetizers:0"
        # FakeLLM returns bacon (not_available) and scallops (found); keys use normalize_query
        assert result["knowledge_store"][normalize_query("bacon")] == "not_available"
        assert result["knowledge_store"][normalize_query("scallops")].startswith("found:")
    run_async(_run())


def test_item_worker_emits_item_started_progress_event() -> None:
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def fake_safe_invoke(messages: list[Any], **_: Any) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "SaveItemResult",
                    "args": {
                        "estimation": {
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
                        }
                    },
                    "id": "save-1",
                }
            ],
        )

    async def sink(event) -> None:
        emitted.append((event.event, event.data))

    node._safe_invoke = fake_safe_invoke  # type: ignore[method-assign]

    async def _run() -> None:
        async with bind_progress_event_sink(sink):
            await node(
                EstimationState(
                    estimation_id="est-1",
                    menu_spec=sample_menu_spec(),
                    completed_items=[],
                    knowledge_store={},
                    status="in_progress",
                )
            )

        assert emitted[0][0] == "item_started"
        assert emitted[0][1]["item_name"] == "Duplicate Dish"
        assert emitted[0][1]["item_key"] == "appetizers:0"

    run_async(_run())


def test_item_worker_sets_failed_when_validation_fails() -> None:
    """When validate_item_estimation returns errors, completed item has status failed."""
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]

    def mock_validate(*args, **kwargs):
        return ["Simulated validation error"]

    with patch("app.agent.nodes.batch_worker.validate_item_estimation", side_effect=mock_validate):
        result = run_async(
            node(
                EstimationState(
                    estimation_id="est-1",
                    menu_spec=sample_menu_spec(),
                    completed_items=[],
                    knowledge_store={},
                    status="in_progress",
                )
            )
        )

    assert len(result["completed_items"]) >= 1
    assert result["completed_items"][0]["status"] == "failed"
    assert result["completed_items"][0]["item_key"] == "appetizers:0"


def test_estimation_state_accepts_string_keyed_price_cache() -> None:
    state = EstimationState(
        estimation_id="est-1",
        menu_spec=sample_menu_spec(),
        completed_items=[],
        knowledge_store={},
        memo_store={
            "price_cache": {
                "7067228::1 each": {
                    "sysco_item_number": "7067228",
                    "quantity_needed": "1 each",
                    "unit_cost": 5.71,
                }
            }
        },
        status="in_progress",
    )
    assert "7067228::1 each" in state.memo_store["price_cache"]


def test_item_worker_does_not_emit_quote_when_last_item_finishes() -> None:
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]

    async def fake_safe_invoke(messages: list[Any], **_: Any) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "SaveItemResult",
                    "args": {
                        "estimation": {
                            "item_name": "Only Dish",
                            "category": "appetizers",
                            "ingredients": [
                                {
                                    "name": "Salt",
                                    "quantity": "1 tsp",
                                    "unit_cost": 0.05,
                                    "source": "estimated",
                                    "sysco_item_number": None,
                                },
                                {
                                    "name": "Butter",
                                    "quantity": "1 tbsp",
                                    "unit_cost": 0.08,
                                    "source": "sysco_catalog",
                                    "sysco_item_number": "123",
                                },
                            ],
                            "ingredient_cost_per_unit": 0.13,
                        }
                    },
                    "id": "save-only",
                }
            ],
        )

    node._safe_invoke = fake_safe_invoke  # type: ignore[method-assign]

    async def _run() -> None:
        result = await node(
            EstimationState(
                estimation_id="est-1",
                menu_spec={
                    "event": "One",
                    "categories": {
                        "appetizers": [
                            {
                                "name": "Only Dish",
                                "description": "One item",
                            }
                        ]
                    },
                },
                completed_items=[],
                knowledge_store={},
                status="in_progress",
            )
        )

        assert "quote" not in result

    run_async(_run())
