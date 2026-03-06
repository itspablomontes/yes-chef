"""LangGraph state schemas for the estimation graph.

Two levels of state:
- EstimationState: top-level graph state (estimation job lifetime)
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field


def _add_items(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reducer: append new completed items to existing list."""
    return existing + new


class ItemEstimation(BaseModel):
    """Structured output schema for an item worker's result.

    This is what the LLM returns via with_structured_output().
    """

    item_name: str = Field(description="Menu item name exactly as it appears in the specification")
    category: str = Field(description="Category: appetizers, main_plates, desserts, or cocktails")
    ingredients: list[IngredientCostOutput] = Field(description="All ingredients with costs")
    ingredient_cost_per_unit: float = Field(description="Total ingredient cost per serving")


class IngredientCostOutput(BaseModel):
    """Structured output for a single ingredient cost."""

    name: str = Field(description="Ingredient name")
    quantity: str = Field(description="Quantity per serving (e.g., '8 oz', '2 each')")
    unit_cost: float | None = Field(description="Cost for the specified quantity, null if unavailable")
    source: str = Field(description="One of: sysco_catalog, estimated, not_available")
    sysco_item_number: str | None = Field(default=None, description="Sysco item number if matched")


# Fix forward reference — ItemEstimation uses IngredientCostOutput
ItemEstimation.model_rebuild()


class EstimationState(BaseModel):
    """Top-level graph state for the entire estimation job.

    The completed_items field uses a custom reducer to append new
    results as workers complete.
    """

    estimation_id: str = ""
    menu_spec: dict[str, Any] = Field(default_factory=dict)
    completed_items: Annotated[list[dict[str, Any]], _add_items] = Field(default_factory=list)
    knowledge_store: dict[str, str] = Field(default_factory=dict)
    memo_store: dict[str, dict[str, Any]] = Field(default_factory=dict)
    global_catalog_cache: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Shared catalog resolution cache across menu items (normalized_name -> match)",
    )
    status: str = "pending"
    quote: dict[str, Any] = Field(default_factory=dict)
