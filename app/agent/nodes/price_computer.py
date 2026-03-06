"""Deterministic batch price computation — calculates per-serving costs from resolved ingredients."""

from __future__ import annotations

from typing import Any

from app.agent.tools.get_item_price import get_item_price


def _price_cache_key(sysco_item_number: str, quantity_needed: str) -> str:
    """Stable cache key for price lookups."""
    return f"{sysco_item_number}::{quantity_needed}"


class PriceComputerNode:
    """Computes per-serving costs for resolved ingredients. Uses price cache for deduplication."""

    def compute(
        self,
        resolved_ingredients: list[dict[str, Any]],
        price_cache: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compute unit costs for all resolved ingredients. Returns priced list and total."""
        cache = dict(price_cache) if price_cache else {}
        priced: list[dict[str, Any]] = []
        total = 0.0
        lookup_count = 0

        for ing in resolved_ingredients:
            name = ing.get("name", "")
            quantity = ing.get("quantity_needed", "")
            source = ing.get("source", "")
            sysco_item_number = ing.get("sysco_item_number")

            out = {
                "name": name,
                "quantity": quantity,
                "unit_cost": ing.get("unit_cost"),
                "source": source,
                "sysco_item_number": sysco_item_number,
            }

            if source == "sysco_catalog" and sysco_item_number and quantity:
                key = _price_cache_key(sysco_item_number, quantity)
                if key in cache:
                    result = cache[key]
                else:
                    lookup_count += 1
                    result = get_item_price.invoke({
                        "sysco_item_number": sysco_item_number,
                        "quantity_needed": quantity,
                    })
                    cache[key] = result

                unit_cost = result.get("unit_cost")
                out["unit_cost"] = unit_cost
                if unit_cost is not None:
                    total += float(unit_cost)
            elif source == "estimated" and ing.get("unit_cost") is not None:
                total += float(ing["unit_cost"])

            priced.append(out)

        return {
            "priced_ingredients": priced,
            "ingredient_cost_per_unit": round(total, 2),
            "item_stage": "price_computation",
            "price_cache": cache,
            "price_lookup_count": lookup_count,
        }
