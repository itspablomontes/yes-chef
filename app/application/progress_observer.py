"""Observer protocol and persistence observer.

Decouples side effects (DB writes) from graph execution.
The orchestrator fires events; observers react to them.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.domain.entities import ItemResult
from app.domain.repositories import EstimationRepository, ItemResultRepository
from app.domain.value_objects import EstimationStatus

logger = logging.getLogger(__name__)


class EstimationObserver(Protocol):
    """Interface for estimation event observers."""

    async def on_item_complete(
        self, estimation_id: str, item_data: dict[str, Any]
    ) -> None: ...

    async def on_estimation_complete(
        self, estimation_id: str, quote: dict[str, Any]
    ) -> None: ...

    async def on_error(
        self, estimation_id: str, error: str
    ) -> None: ...


class ProgressObserver:
    """Persists estimation progress to the database.

    Listens to events from the orchestrator and writes to
    the DB via domain repositories. This observer pattern
    keeps the graph execution clean of persistence concerns.
    """

    def __init__(
        self,
        estimation_repo: EstimationRepository,
        item_result_repo: ItemResultRepository,
    ) -> None:
        self._estimation_repo = estimation_repo
        self._item_result_repo = item_result_repo

    async def on_item_complete(
        self, estimation_id: str, item_data: dict[str, Any]
    ) -> None:
        """Persist a completed item result and update progress."""
        import uuid
        from datetime import datetime

        # Build domain entity from graph state data
        from app.domain.value_objects import IngredientCost, IngredientSource

        ingredients = [
            IngredientCost(
                name=str(ing.get("name", "")),
                quantity=str(ing.get("quantity", "")),
                unit_cost=float(str(ing.get("unit_cost", 0))) if ing.get("unit_cost") is not None else None,
                source=IngredientSource(str(ing.get("source", "not_available"))),
                sysco_item_number=str(ing["sysco_item_number"]) if ing.get("sysco_item_number") else None,
            )
            for ing in item_data.get("ingredients", [])
        ]

        result = ItemResult(
            id=str(uuid.uuid4()),
            estimation_id=estimation_id,
            item_name=str(item_data.get("item_name", "")),
            category=str(item_data.get("category", "")),
            ingredients=ingredients,
            ingredient_cost_per_unit=float(item_data.get("ingredient_cost_per_unit") or 0.0),
            item_key=str(item_data.get("item_key")) if item_data.get("item_key") else None,
            status=str(item_data.get("status", "completed")),
            completed_at=datetime.now(),
        )

        saved_result = await self._item_result_repo.save(result)
        if saved_result.id != result.id:
            logger.info(
                "Item '%s' already persisted for key %s; skipping duplicate progress update",
                saved_result.item_name,
                saved_result.item_key,
            )
            return

        # Fetch current job to increment completed count
        job = await self._estimation_repo.get(estimation_id)
        if job:
            new_count = job.items_completed + 1
            await self._estimation_repo.update_progress(estimation_id, new_count)
            logger.info(
                "Item '%s' saved (%d/%d)",
                result.item_name, new_count, job.total_items,
            )

    async def on_estimation_complete(
        self, estimation_id: str, quote: dict[str, Any]
    ) -> None:
        """Persist the final quote and update job status."""
        # Determine status based on quote content
        failed_items = quote.get("failed_items", [])
        if failed_items:
            status = EstimationStatus.COMPLETED_WITH_FAILURES
        else:
            status = EstimationStatus.COMPLETED

        await self._estimation_repo.update_status(estimation_id, status)
        await self._estimation_repo.update_quote(estimation_id, quote)
        logger.info("Estimation %s completed with status: %s", estimation_id, status.value)

    async def on_error(
        self, estimation_id: str, error: str
    ) -> None:
        """Mark estimation as failed."""
        logger.error("Estimation %s failed: %s", estimation_id, error)
        await self._estimation_repo.update_status(
            estimation_id, EstimationStatus.FAILED
        )
