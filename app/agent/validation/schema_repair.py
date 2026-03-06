"""Deterministic schema repair — fix common failures without LLM retries."""

from __future__ import annotations

from typing import Any

_VALID_CATEGORIES = {"appetizers", "main_plates", "desserts", "cocktails"}
_VALID_SOURCES = {"sysco_catalog", "estimated", "not_available"}


def repair_line_item(
    line: dict[str, Any],
    menu_category: str | None = None,
) -> dict[str, Any]:
    """Repair common schema issues in a line item."""
    out = dict(line)

    ingredients = out.get("ingredients", [])
    if not isinstance(ingredients, list):
        ingredients = []

    total = sum(float(ing.get("unit_cost", 0) or 0) for ing in ingredients)
    if out.get("ingredient_cost_per_unit") is None and total >= 0:
        out["ingredient_cost_per_unit"] = round(total, 2)

    if menu_category and out.get("category") not in _VALID_CATEGORIES:
        if menu_category in _VALID_CATEGORIES:
            out["category"] = menu_category

    repaired_ingredients: list[dict[str, Any]] = []
    for ing in ingredients:
        ing_copy = dict(ing)
        src = ing_copy.get("source", "")
        if src not in _VALID_SOURCES:
            ing_copy["source"] = "estimated" if ing_copy.get("unit_cost") else "not_available"
        if "quantity_needed" in ing_copy and "quantity" not in ing_copy:
            ing_copy["quantity"] = ing_copy["quantity_needed"]
        repaired_ingredients.append(ing_copy)

    out["ingredients"] = repaired_ingredients
    return out
