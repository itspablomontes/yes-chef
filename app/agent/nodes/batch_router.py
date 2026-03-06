"""Work-unit router conditional edge logic.

Determines whether there are unprocessed items left to process
and routes to either the item worker or the final reduce node.
"""

from __future__ import annotations

from app.agent.state import EstimationState
from app.application.work_units import build_menu_work_units, completed_item_keys


def route_work_item(state: EstimationState) -> str:
    """Determine if there are more items to process.

    Returns:
        "item_worker" if unprocessed items remain.
        "reduce" if all items are completed.
    """
    menu_spec = state.menu_spec
    work_units = build_menu_work_units(menu_spec)
    done_keys = completed_item_keys(menu_spec, state.completed_items)

    for unit in work_units:
        item_key = unit.get("item_key")
        if isinstance(item_key, str) and item_key not in done_keys:
            return "item_worker"

    return "reduce"


route_batch = route_work_item
