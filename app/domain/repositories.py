"""Repository interfaces (Ports) for the Yes Chef domain.

Uses Python's Protocol (structural subtyping) instead of ABCs.
Any class with matching method signatures satisfies the contract —
no explicit inheritance required.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.entities import EstimationJob, ItemResult
from app.domain.value_objects import EstimationStatus


class EstimationRepository(Protocol):
    """Port for estimation job persistence."""

    async def create(self, job: EstimationJob) -> EstimationJob: ...

    async def get(self, estimation_id: str) -> EstimationJob | None: ...

    async def update_progress(
        self, estimation_id: str, items_completed: int
    ) -> None: ...

    async def update_status(
        self, estimation_id: str, status: EstimationStatus
    ) -> None: ...

    async def update_quote(
        self, estimation_id: str, quote_json: dict[str, object]
    ) -> None: ...


class ItemResultRepository(Protocol):
    """Port for per-item result persistence."""

    async def save(self, result: ItemResult) -> ItemResult: ...

    async def get_by_estimation(
        self, estimation_id: str
    ) -> list[ItemResult]: ...
