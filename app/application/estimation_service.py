"""Estimation service — application entry point.

Coordinates creating/resuming estimations by wiring together
the orchestrator, observer, knowledge store, and repositories.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.application.estimation_orchestrator import EstimationOrchestrator
from app.application.knowledge_store import KnowledgeStore
from app.application.progress_observer import ProgressObserver
from app.application.work_units import align_completed_items
from app.domain.entities import EstimationJob
from app.domain.repositories import EstimationRepository, ItemResultRepository
from app.domain.value_objects import EstimationStatus

logger = logging.getLogger(__name__)


class EstimationService:
    """High-level service for creating and resuming estimations.

    This is the entry point that the presentation layer calls.
    It orchestrates the graph, observer, and knowledge store.
    """

    def __init__(
        self,
        graph: CompiledStateGraph,
        estimation_repo: EstimationRepository,
        item_result_repo: ItemResultRepository,
    ) -> None:
        self._graph = graph
        self._estimation_repo = estimation_repo
        self._item_result_repo = item_result_repo

    async def create_estimation(
        self, menu_spec: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Create a new estimation and stream results.

        Steps:
        1. Create an EstimationJob in the DB
        2. Build initial graph state with empty knowledge
        3. Wire up the observer for persistence
        4. Stream graph execution
        """
        estimation_id = str(uuid.uuid4())

        # Parse menu spec for metadata
        event_name = menu_spec.get("event", "Unknown Event")
        categories: dict[str, Any] = menu_spec.get("categories", {})
        total_items = sum(
            len(items) for items in categories.values() if isinstance(items, list)
        )

        # Create job record
        job = EstimationJob(
            id=estimation_id,
            event_name=event_name,
            total_items=total_items,
            items_completed=0,
            status=EstimationStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            menu_spec_json=menu_spec,
        )
        await self._estimation_repo.create(job)
        await self._estimation_repo.update_status(
            estimation_id, EstimationStatus.IN_PROGRESS
        )

        logger.info(
            "Created estimation %s: '%s' with %d items",
            estimation_id, event_name, total_items,
        )

        # Build initial state
        initial_state: dict[str, Any] = {
            "estimation_id": estimation_id,
            "menu_spec": menu_spec,
            "completed_items": align_completed_items(menu_spec, []),
            "knowledge_store": {},
            "status": "in_progress",
        }

        # Wire up orchestrator with observer
        orchestrator = EstimationOrchestrator(self._graph)
        observer = ProgressObserver(self._estimation_repo, self._item_result_repo)
        orchestrator.add_observer(observer)

        # Stream execution
        async for event in orchestrator.stream(estimation_id, initial_state):
            yield event

    async def resume_estimation(
        self, estimation_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Resume a partially completed estimation.

        Steps:
        1. Load the job and completed items from DB
        2. Reconstruct the knowledge store from completed items
        3. Build state with completed items already populated
        4. Stream remaining items
        """
        job = await self._estimation_repo.get(estimation_id)
        if job is None:
            yield {
                "event": "error",
                "data": {"message": f"Estimation {estimation_id} not found"},
            }
            return

        # Load completed items
        completed_items = await self._item_result_repo.get_by_estimation(estimation_id)

        # Reconstruct knowledge from completed items
        knowledge = KnowledgeStore()
        completed_dicts: list[dict[str, Any]] = []
        for item in completed_items:
            item_dict: dict[str, Any] = {
                "item_name": item.item_name,
                "category": item.category,
                "item_key": item.item_key,
                "ingredients": [
                    {
                        "name": ic.name,
                        "quantity": ic.quantity,
                        "unit_cost": ic.unit_cost,
                        "source": ic.source.value,
                        "sysco_item_number": ic.sysco_item_number,
                    }
                    for ic in item.ingredients
                ],
                "ingredient_cost_per_unit": item.ingredient_cost_per_unit,
            }
            completed_dicts.append(item_dict)

        completed_dicts = align_completed_items(job.menu_spec_json, completed_dicts)
        knowledge.reconstruct_from_items(completed_dicts)

        remaining = job.total_items - len(completed_items)
        logger.info(
            "Resuming estimation %s: %d/%d complete, %d remaining, %d known ingredients",
            estimation_id, len(completed_items), job.total_items,
            remaining, knowledge.size,
        )

        # Build state with completed items for the coordinator to filter
        initial_state: dict[str, Any] = {
            "estimation_id": estimation_id,
            "menu_spec": job.menu_spec_json,
            "completed_items": completed_dicts,
            "knowledge_store": knowledge.get_hints(),
            "status": "in_progress",
        }

        # Wire up orchestrator
        orchestrator = EstimationOrchestrator(self._graph)
        observer = ProgressObserver(self._estimation_repo, self._item_result_repo)
        orchestrator.add_observer(observer)

        async for event in orchestrator.stream(estimation_id, initial_state):
            yield event

    async def get_estimation(self, estimation_id: str) -> dict[str, Any] | None:
        """Get the current status of an estimation."""
        job = await self._estimation_repo.get(estimation_id)
        if job is None:
            return None

        return {
            "id": job.id,
            "event_name": job.event_name,
            "total_items": job.total_items,
            "items_completed": job.items_completed,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "quote": job.quote_json,
        }
