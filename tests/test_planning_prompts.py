"""Tests for planning prompt and knowledge hints."""

from __future__ import annotations

from app.agent.contracts.item_pipeline import IngredientPlanPayload, PlannedIngredient
from app.agent.nodes.ingredient_planner import IngredientPlannerNode
from app.agent.prompts.planning_prompt import format_knowledge_hints
from tests.helpers import run_async


def test_format_knowledge_hints_empty() -> None:
    assert format_knowledge_hints({}) == ""
    assert format_knowledge_hints({"x": ""}) == ""


def test_format_knowledge_hints_includes_not_available() -> None:
    result = format_knowledge_hints({"beef wagyu": "not_available"})
    assert "beef wagyu" in result
    assert "not available" in result
    assert "needs_catalog_lookup: false" in result


def test_format_knowledge_hints_includes_estimated_and_found() -> None:
    result = format_knowledge_hints({
        "truffle oil": "estimated",
        "butter": "found:123",
    })
    assert "truffle oil" in result
    assert "estimated" in result
    assert "butter" in result
    assert "found in Sysco" in result


class SpyStructuredLLM:
    """Captures messages passed to ainvoke."""

    def __init__(self) -> None:
        self.captured_messages: list = []

    async def ainvoke(self, messages):
        self.captured_messages = list(messages)
        return {
            "parsed": IngredientPlanPayload(
                ingredients=[
                    PlannedIngredient(name="salt", quantity_needed="1 tsp", needs_catalog_lookup=False),
                ]
            ),
            "raw": type("Obj", (), {
                "response_metadata": {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
                "usage_metadata": None,
            })(),
        }


def test_plan_item_injects_knowledge_hints_into_prompt() -> None:
    """When plan_item is called with knowledge, the prompt includes the hints."""
    spy = SpyStructuredLLM()
    fake_llm = type("FakeLLM", (), {
        "with_structured_output": lambda self, schema, **kw: spy,
    })()

    planner = IngredientPlannerNode(llm=fake_llm)
    unit = {"name": "Wagyu Carpaccio", "description": "Wagyu beef", "category": "appetizers", "item_key": "appetizers:0"}

    run_async(planner.plan_item(unit, knowledge={"beef wagyu": "not_available"}))

    assert len(spy.captured_messages) >= 1
    system_content = spy.captured_messages[0].content
    assert "Known catalog status" in system_content
    assert "beef wagyu" in system_content
    assert "not available" in system_content
