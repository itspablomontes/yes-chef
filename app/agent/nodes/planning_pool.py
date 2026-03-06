"""Parallel planning pool — runs ingredient planning for multiple items concurrently."""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.nodes.ingredient_planner import IngredientPlannerNode
from app.application.work_units import ITEM_KEY_FIELD, build_menu_work_units, completed_item_keys


class PlanningPool:
    """Runs planning for multiple items in parallel with bounded concurrency."""

    def __init__(
        self,
        planner: IngredientPlannerNode,
        max_concurrency: int = 6,
    ) -> None:
        self._planner = planner
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def plan_batch(
        self,
        items: list[dict[str, Any]],
        knowledge: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Plan a batch of items concurrently. Returns list of planning results."""
        async def plan_one(unit: dict[str, Any]) -> dict[str, Any]:
            async with self._semaphore:
                return await self._planner.plan_item(unit, knowledge)

        results = await asyncio.gather(*[plan_one(item) for item in items])
        return list(results)
