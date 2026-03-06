"""Batch router conditional edge logic.

Determines whether there are unprocessed items left to process
and routes to either the batch worker or the final reduce node.
"""

from __future__ import annotations

from typing import Any

from app.agent.state import EstimationState


def route_batch(state: EstimationState) -> str:
    """Determine if there are more items to process.

    Returns:
        "batch_worker" if unprocessed items remain.
        "reduce" if all items are completed.
    """
    menu_spec = state.menu_spec
    categories: dict[str, Any] = menu_spec.get("categories", {})

    # Collect names of already-completed items
    completed_names: set[str] = set()
    for item in state.completed_items:
        if isinstance(item, dict):
            name = item.get("item_name", "")
            if name:
                completed_names.add(name)

    # Check if any item is not in completed_names
    for _category_name, items in categories.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_name = item.get("name", "")
            if item_name and item_name not in completed_names:
                return "batch_worker"

    return "reduce"
