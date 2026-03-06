"""One-shot ingredient planning node (LLM only).

Outputs structured planned ingredients per menu item. No tools, no iteration.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.contracts.item_pipeline import IngredientPlanPayload
from app.agent.prompts.planning_prompt import (
    PLANNING_PROMPT_TEMPLATE,
    build_planning_context,
    format_knowledge_hints,
)
from app.agent.state import EstimationState
from app.application.work_units import ITEM_KEY_FIELD, build_menu_work_units, completed_item_keys


def _extract_usage_from_metadata(metadata: dict[str, Any]) -> dict[str, int | float]:
    """Extract token usage from LLM response_metadata."""
    usage = metadata.get("usage") or metadata.get("token_usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "duration_seconds": float(usage.get("total_time", 0) or usage.get("duration_seconds", 0)),
    }


class IngredientPlannerNode:
    """Plans ingredients for a single menu item via one LLM call."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm
        self._structured_llm = llm.with_structured_output(
            IngredientPlanPayload, include_raw=True
        )

    def _get_next_work_unit(self, state: EstimationState) -> dict[str, Any] | None:
        """Return the next uncompleted work unit."""
        work_units = build_menu_work_units(state.menu_spec)
        completed = completed_item_keys(state.menu_spec, state.completed_items)
        for unit in work_units:
            key = unit.get(ITEM_KEY_FIELD)
            if isinstance(key, str) and key not in completed:
                return unit
        return None

    async def __call__(self, state: EstimationState) -> dict[str, Any]:
        """Plan ingredients for the next uncompleted item. One LLM call."""
        unit = self._get_next_work_unit(state)
        if unit is None:
            return {}

        category = unit.get("category", "appetizers")
        context = build_planning_context(unit, category)
        knowledge_hints = format_knowledge_hints(state.knowledge_store) if state.knowledge_store else ""
        prompt = PLANNING_PROMPT_TEMPLATE.format(
            item_context=context,
            knowledge_hints=knowledge_hints,
        )

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"List the ingredients for: {unit.get('name', 'Unknown')}"),
        ]

        result = await self._structured_llm.ainvoke(messages)
        return self._parse_plan_result(result, unit, category, item_stage="ingredient_plan")

    def _parse_plan_result(
        self,
        result: dict[str, Any],
        unit: dict[str, Any],
        category: str,
        *,
        item_stage: str | None = None,
    ) -> dict[str, Any]:
        """Parse include_raw result and extract usage."""
        parsed = result.get("parsed")
        if parsed is None:
            raise ValueError(
                f"Planning failed: {result.get('parsing_error', 'unknown error')}"
            )
        planned = [p.model_dump() for p in parsed.ingredients]
        usage: dict[str, int | float] = {}
        raw = result.get("raw")
        if hasattr(raw, "response_metadata") and isinstance(
            getattr(raw, "response_metadata"), dict
        ):
            usage = _extract_usage_from_metadata(raw.response_metadata)
        if not usage or usage.get("total_tokens", 0) == 0:
            um = getattr(raw, "usage_metadata", None)
            if isinstance(um, dict):
                usage = _extract_usage_from_metadata({"usage": um})
            elif um is not None and hasattr(um, "get"):
                usage = _extract_usage_from_metadata({"usage": dict(um)})

        out: dict[str, Any] = {
            "planned_ingredients": planned,
            "item_key": unit.get(ITEM_KEY_FIELD),
            "item_name": unit.get("name"),
            "category": category,
            "usage": usage,
        }
        if item_stage:
            out["item_stage"] = item_stage
        return out

    async def plan_item(
        self,
        unit: dict[str, Any],
        knowledge: dict[str, str],
    ) -> dict[str, Any]:
        """Plan a single item. Used by PlanningPool for parallel execution."""
        category = unit.get("category", "appetizers")
        context = build_planning_context(unit, category)
        knowledge_hints = format_knowledge_hints(knowledge) if knowledge else ""
        prompt = PLANNING_PROMPT_TEMPLATE.format(
            item_context=context,
            knowledge_hints=knowledge_hints,
        )

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"List the ingredients for: {unit.get('name', 'Unknown')}"),
        ]

        result = await self._structured_llm.ainvoke(messages)
        return self._parse_plan_result(result, unit, category)
