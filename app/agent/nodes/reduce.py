"""Reduce node — collect all item results and assemble the final quote.

The reduce step runs after all item workers complete. It aggregates
results into the Quote structure conforming to quote_schema.json.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.agent.state import EstimationState


def reduce(state: EstimationState) -> dict[str, Any]:
    """Collect completed items and assemble the final quote."""
    completed = state.completed_items
    menu_spec = state.menu_spec

    # Separate successful and failed items
    successful: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in completed:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "failed":
            failed.append(item)
        else:
            successful.append(item)

    # Build line_items for the quote
    line_items: list[dict[str, Any]] = []
    for item in successful:
        line_items.append({
            "item_name": item.get("item_name", ""),
            "category": item.get("category", ""),
            "ingredients": item.get("ingredients", []),
            "ingredient_cost_per_unit": item.get("ingredient_cost_per_unit", 0.0),
        })

    # Determine final status
    if not failed:
        status = "completed"
    elif successful:
        status = "completed_with_failures"
    else:
        status = "failed"

    quote: dict[str, Any] = {
        "quote_id": str(uuid.uuid4()),
        "event": menu_spec.get("event", ""),
        "generated_at": datetime.now(UTC).isoformat(),
        "line_items": line_items,
    }

    if failed:
        quote["failed_items"] = [
            {"item_name": f.get("item_name", ""), "category": f.get("category", "")}
            for f in failed
        ]

    return {
        "status": status,
        "completed_items": [],  # Don't duplicate — already in state
        "quote": quote,
    }
