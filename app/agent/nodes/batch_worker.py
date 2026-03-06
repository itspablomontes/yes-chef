"""Plan-Then-Batch-Price worker node.

Processes menu items in batches: parallel planning, batch catalog resolution
with global cache, parallel pricing, deterministic repair.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI

from app.agent.nodes.catalog_resolver import CatalogResolverNode
from app.agent.nodes.global_catalog_cache import GlobalCatalogCache
from app.agent.nodes.ingredient_planner import IngredientPlannerNode
from app.agent.nodes.planning_pool import PlanningPool
from app.agent.nodes.price_computer import PriceComputerNode
from app.agent.state import EstimationState
from app.agent.validation.schema_repair import repair_line_item
from app.agent.validation.validators import validate_item_estimation
from app.application.stream_events import emit_progress_event
from app.application.work_units import ITEM_KEY_FIELD, build_menu_work_units, completed_item_keys
from app.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)


class ItemWorkerNode:
    """Processes menu items via Plan-Then-Batch-Price: plan in parallel, batch resolve, price in parallel."""

    def __init__(self, llm: ChatOpenAI, repair_llm: ChatOpenAI | None = None) -> None:
        self._llm = llm
        settings = get_settings()
        self._planner = IngredientPlannerNode(llm=llm)
        self._planning_pool = PlanningPool(
            planner=self._planner,
            max_concurrency=max(1, settings.planning_pool_size),
        )
        self._resolver = CatalogResolverNode()
        self._price_computer = PriceComputerNode()

    def _get_next_work_units(
        self,
        state: EstimationState,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Identify unfinished menu items in stable order up to limit."""
        work_units = build_menu_work_units(state.menu_spec)
        completed_keys = completed_item_keys(state.menu_spec, state.completed_items)
        selected: list[dict[str, Any]] = []

        for unit in work_units:
            item_key = unit.get(ITEM_KEY_FIELD)
            if isinstance(item_key, str) and item_key not in completed_keys:
                selected.append(unit)
                if len(selected) >= limit:
                    break

        return selected

    def _build_terminal_update(
        self,
        *,
        completed_items: list[dict[str, Any]],
        knowledge_store: dict[str, str],
        memo_store: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the completed item update for the current work unit(s)."""
        return {
            "completed_items": completed_items,
            "knowledge_store": knowledge_store,
            "memo_store": memo_store,
        }

    @staticmethod
    def _item_sort_key(item: dict[str, Any]) -> tuple[str, int]:
        item_key = str(item.get(ITEM_KEY_FIELD, "zzz:999999"))
        if ":" not in item_key:
            return item_key, 0
        prefix, index = item_key.split(":", 1)
        try:
            return prefix, int(index)
        except ValueError:
            return prefix, 0

    async def _process_batch_staged(
        self,
        state: EstimationState,
        work_units: list[dict[str, Any]],
        completed_count: int,
    ) -> dict[str, Any]:
        """Process a batch via plan → batch resolve → price in parallel."""
        catalog_cache = GlobalCatalogCache(cache=dict(state.global_catalog_cache))
        price_cache = dict(state.memo_store.get("price_cache", {}))
        total_catalog_lookups = 0
        total_price_lookups = 0

        planning_results = await self._planning_pool.plan_batch(
            work_units,
            state.knowledge_store,
        )

        completed_items: list[dict[str, Any]] = []
        new_knowledge = dict(state.knowledge_store)

        for plan_result in planning_results:
            item_key = plan_result.get("item_key", "")
            item_name = plan_result.get("item_name", "Unknown")
            category = plan_result.get("category", "appetizers")
            planned = plan_result.get("planned_ingredients", [])

            resolved = self._resolver.resolve(
                planned,
                cache=catalog_cache,
                max_results=get_settings().tool_result_max_matches,
            )
            total_catalog_lookups = catalog_cache.resolve_count

            priced = self._price_computer.compute(
                resolved["resolved_ingredients"],
                price_cache,
            )
            price_cache = priced["price_cache"]
            total_price_lookups += priced.get("price_lookup_count", 0)

            line = {
                "item_name": item_name,
                "category": category,
                ITEM_KEY_FIELD: item_key,
                "ingredients": priced["priced_ingredients"],
                "ingredient_cost_per_unit": priced["ingredient_cost_per_unit"],
            }
            repaired = repair_line_item(line, category)

            errors = validate_item_estimation(
                item_name=str(repaired.get("item_name", "")),
                category=str(repaired.get("category", "")),
                ingredients=repaired.get("ingredients", []),
                ingredient_cost_per_unit=float(
                    repaired.get("ingredient_cost_per_unit") or 0
                ),
            )

            telemetry = {
                "llm_calls": 1,
                "tool_calls": 0,
                "item_key": item_key,
                "item_name": item_name,
            }

            completed_count += 1
            await emit_progress_event(
                "item_started",
                estimation_id=state.estimation_id,
                batch_index=completed_count,
                item_name=item_name,
                item_key=item_key,
                completed_items=completed_count - 1,
            )

            if errors:
                repaired["status"] = "failed"
            else:
                for ing in repaired.get("ingredients", []):
                    source = ing.get("source")
                    name = ing.get("name")
                    sysco_item_number = ing.get("sysco_item_number")
                    norm = str(name).lower().strip() if name else ""
                    if norm and source == "not_available":
                        new_knowledge[norm] = "not_available"
                    elif norm and source == "estimated":
                        new_knowledge[norm] = "estimated"
                    elif norm and source == "sysco_catalog" and sysco_item_number:
                        new_knowledge[norm] = f"found:{sysco_item_number}"

            total_tool_calls = total_catalog_lookups + total_price_lookups
            item_telemetry = dict(telemetry)
            item_telemetry["tool_calls"] = (
                total_tool_calls if len(completed_items) == 0 else 0
            )
            repaired["telemetry"] = item_telemetry
            completed_items.append(repaired)

        if len(new_knowledge) > 30:
            new_knowledge = dict(list(new_knowledge.items())[-30:])

        return self._build_terminal_update(
            completed_items=sorted(completed_items, key=self._item_sort_key),
            knowledge_store=new_knowledge,
            memo_store={"search_cache": {}, "price_cache": price_cache},
        ) | {"global_catalog_cache": catalog_cache.to_dict()}

    async def __call__(self, state: EstimationState) -> dict[str, Any]:
        """Execute the staged pipeline for up to N unfinished items."""
        batch_limit = max(1, get_settings().batch_size)
        work_units = self._get_next_work_units(state, limit=batch_limit)
        if not work_units:
            return {}
        completed_count = len(state.completed_items)
        return await self._process_batch_staged(
            state=state,
            work_units=work_units,
            completed_count=completed_count,
        )


BatchWorkerNode = ItemWorkerNode
