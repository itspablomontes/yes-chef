"""Typed contracts for the staged Plan-Then-Batch-Price pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlannedIngredient(BaseModel):
    """Single ingredient from the planning stage."""

    name: str = Field(description="Ingredient name")
    quantity_needed: str = Field(description="Quantity per serving (e.g., '8 oz', '2 each')")
    needs_catalog_lookup: bool = Field(
        default=True,
        description="Whether to look up in Sysco catalog; false for salt/pepper/etc.",
    )


class IngredientPlanPayload(BaseModel):
    """Output of the ingredient planning stage."""

    ingredients: list[PlannedIngredient] = Field(
        default_factory=list,
        description="All planned ingredients for the menu item",
    )
