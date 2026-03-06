"""One-shot ingredient planning node (LLM only).

Outputs structured planned ingredients per menu item. No tools, no iteration.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.contracts.item_pipeline import IngredientPlanPayload, PlannedIngredient
from app.agent.prompts.planning_prompt import PLANNING_PROMPT_TEMPLATE, build_planning_context
from app.agent.state import EstimationState
from app.application.work_units import ITEM_KEY_FIELD, build_menu_work_units, completed_item_keys


class IngredientPlannerNode:
    """Plans ingredients for a single menu item via one LLM call."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm
        self._structured_llm = llm.with_structured_output(IngredientPlanPayload)

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
        prompt = PLANNING_PROMPT_TEMPLATE.format(item_context=context)

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"List the ingredients for: {unit.get('name', 'Unknown')}"),
        ]

        payload: IngredientPlanPayload = await self._structured_llm.ainvoke(messages)
        planned = [p.model_dump() for p in payload.ingredients]

        return {
            "planned_ingredients": planned,
            "item_key": unit.get(ITEM_KEY_FIELD),
            "item_name": unit.get("name"),
            "category": category,
            "item_stage": "ingredient_plan",
        }

    async def plan_item(
        self,
        unit: dict[str, Any],
        knowledge: dict[str, str],
    ) -> dict[str, Any]:
        """Plan a single item. Used by PlanningPool for parallel execution."""
        category = unit.get("category", "appetizers")
        context = build_planning_context(unit, category)
        prompt = PLANNING_PROMPT_TEMPLATE.format(item_context=context)

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"List the ingredients for: {unit.get('name', 'Unknown')}"),
        ]

        payload: IngredientPlanPayload = await self._structured_llm.ainvoke(messages)
        planned = [p.model_dump() for p in payload.ingredients]

        return {
            "planned_ingredients": planned,
            "item_key": unit.get(ITEM_KEY_FIELD),
            "item_name": unit.get("name"),
            "category": category,
        }
