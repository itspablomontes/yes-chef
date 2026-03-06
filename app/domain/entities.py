"""Domain entities for the Yes Chef system.

Pure Python dataclasses with zero external dependencies.
Entities have identity (id fields) and represent core business concepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.value_objects import EstimationStatus, IngredientCost


@dataclass(slots=True)
class MenuItem:
    """A single item from the event menu specification."""

    name: str
    description: str
    dietary_notes: str | None = None
    service_style: str | None = None


@dataclass(slots=True)
class MenuSpec:
    """Full menu specification for a catering event."""

    event: str
    date: str
    venue: str
    guest_count_estimate: int
    notes: str
    categories: dict[str, list[MenuItem]]

    def total_items(self) -> int:
        """Total number of menu items across all categories."""
        return sum(len(items) for items in self.categories.values())


@dataclass(slots=True)
class LineItem:
    """A single line item in the final quote."""

    item_name: str
    category: str
    ingredients: list[IngredientCost]
    ingredient_cost_per_unit: float


@dataclass(slots=True)
class Quote:
    """Final output: a priced quote for the entire event."""

    quote_id: str
    event: str
    generated_at: datetime
    line_items: list[LineItem]


@dataclass(slots=True)
class EstimationJob:
    """Tracks the lifecycle of an estimation request.

    Persisted to the database for resumability.
    """

    id: str
    event_name: str
    total_items: int
    items_completed: int
    status: EstimationStatus
    created_at: datetime
    updated_at: datetime
    menu_spec_json: dict[str, object] = field(default_factory=dict)
    quote_json: dict[str, object] | None = None


@dataclass(slots=True)
class ItemResult:
    """Result of processing a single menu item.

    Persisted per-item for resumability — if the system is interrupted,
    completed items are not reprocessed.
    """

    id: str
    estimation_id: str
    item_name: str
    category: str
    ingredients: list[IngredientCost]
    ingredient_cost_per_unit: float
    item_key: str | None = None
    status: str = "completed"
    completed_at: datetime = field(default_factory=datetime.now)
