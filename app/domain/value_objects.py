"""Value objects for the Yes Chef domain.

Pure Python types with zero external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IngredientSource(StrEnum):
    """Where an ingredient's price data originated."""

    SYSCO_CATALOG = "sysco_catalog"
    ESTIMATED = "estimated"
    NOT_AVAILABLE = "not_available"


class EstimationStatus(StrEnum):
    """Lifecycle status of an estimation job."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngredientCost:
    """Cost breakdown for a single ingredient in a dish.

    Immutable value object — identity doesn't matter, only the values.
    """

    name: str
    quantity: str
    unit_cost: float | None
    source: IngredientSource
    sysco_item_number: str | None = None
