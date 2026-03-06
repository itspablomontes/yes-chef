"""Programmatic validation for LLM-produced item estimations.

Zero-cost sanity checks that catch impossible outputs before
trusting them. Per ADR-005: programmatic checks cover the 80/20
without additional LLM calls.
"""

from __future__ import annotations

from typing import Any


def validate_item_estimation(
    item_name: str,
    category: str,
    ingredients: list[dict[str, Any]],
    ingredient_cost_per_unit: float,
) -> list[str]:
    """Validate a structured item estimation result.

    Returns a list of error strings. Empty list = valid.
    """
    errors: list[str] = []

    # Check 1: At least 2 ingredients per dish
    if len(ingredients) < 2:
        errors.append(
            f"Expected at least 2 ingredients for '{item_name}', got {len(ingredients)}."
        )

    # Check 2: unit_cost > 0 when source = sysco_catalog
    for ing in ingredients:
        source = ing.get("source", "")
        unit_cost = ing.get("unit_cost")
        name = ing.get("name", "unknown")

        if source == "sysco_catalog" and (unit_cost is None or unit_cost <= 0):
            errors.append(
                f"Ingredient '{name}' is sourced from sysco_catalog but has "
                f"invalid unit_cost: {unit_cost}. Must be > 0."
            )

    # Check 3: ingredient_cost_per_unit ≈ sum of unit_costs (within 10% tolerance)
    total = sum(
        float(ing.get("unit_cost", 0) or 0) for ing in ingredients
    )
    if total > 0 and abs(ingredient_cost_per_unit - total) / total > 0.10:
        errors.append(
            f"ingredient_cost_per_unit (${ingredient_cost_per_unit:.2f}) does not "
            f"match sum of unit_costs (${total:.2f}). Difference > 10%."
        )

    # Check 4: ingredient_cost_per_unit must not be negative
    if ingredient_cost_per_unit < 0:
        errors.append(
            f"ingredient_cost_per_unit cannot be negative: ${ingredient_cost_per_unit:.2f}"
        )

    # Check 4.5: Semantic Bound (Unit Extraction Hallucination prevention)
    # The math is deterministic but the unit conversion reasoning is LLM-based. 
    # If it costs >$75 per serving for a single item, the LLM probably requested "1 Case" instead of "1 Each".
    if ingredient_cost_per_unit > 75.0:
        errors.append(
            f"ingredient_cost_per_unit (${ingredient_cost_per_unit:.2f}) is suspiciously high. "
            f"Double-check that your unit extraction (e.g., asking for 'Cases' instead of 'Ounces') is correct."
        )

    # Check 5: No duplicate ingredient names
    names = [str(ing.get("name", "")).lower().strip() for ing in ingredients]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            errors.append(f"Duplicate ingredient name: '{name}'")
        seen.add(name)

    # Check 6: Valid category
    valid_categories = {"appetizers", "main_plates", "desserts", "cocktails"}
    if category not in valid_categories:
        errors.append(
            f"Invalid category '{category}'. Must be one of: {valid_categories}"
        )

    return errors
