from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.agent.nodes.batch_worker import ItemWorkerNode
from app.agent.state import EstimationState
from app.application.stream_events import bind_progress_event_sink
from tests.helpers import collect_events, run_async, sample_menu_spec


class FakeLLM:
    def bind_tools(self, tools: list[Any]) -> "FakeLLM":
        return self


def test_item_worker_processes_one_item_and_updates_knowledge() -> None:
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]

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

    node._safe_invoke = fake_safe_invoke  # type: ignore[method-assign]

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
        assert len(completed) == 1
        assert completed[0]["item_name"] == "Duplicate Dish"
        assert completed[0]["item_key"] == "appetizers:0"
        assert result["knowledge_store"]["sea salt"] == "estimated"
        assert result["knowledge_store"]["butter"] == "found:123"

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


def test_batch_worker_normalizes_provider_tool_arguments() -> None:
    args = ItemWorkerNode._normalize_tool_args(
        "get_item_price",
        {"sysco_item_number": "123", "parameters": "1.5 oz"},
    )
    search_args = ItemWorkerNode._normalize_tool_args(
        "search_catalog",
        {"parameters": "diver scallops"},
    )

    assert args["quantity_needed"] == "1.5 oz"
    assert search_args == {"query": "diver scallops"}


def test_item_worker_serializes_tool_result_as_json() -> None:
    assert ItemWorkerNode._serialize_tool_result({"unit_cost": 3.95, "ok": True}, max_matches=3) == '{"ok": true, "unit_cost": 3.95}'


def test_item_worker_returns_failed_item_when_no_save_tool_call() -> None:
    node = ItemWorkerNode(FakeLLM())  # type: ignore[arg-type]

    async def fake_safe_invoke(messages: list[Any], **_: Any) -> AIMessage:
        return AIMessage(content="No tool calls", tool_calls=[])

    node._safe_invoke = fake_safe_invoke  # type: ignore[method-assign]

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

        assert result["completed_items"][0]["status"] == "failed"
        assert result["completed_items"][0]["item_key"] == "appetizers:0"

    run_async(_run())


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
