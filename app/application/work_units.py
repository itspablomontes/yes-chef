"""Helpers for deriving stable menu work units across create/resume flows."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any

ITEM_KEY_FIELD = "item_key"


def build_menu_work_units(menu_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the menu into ordered work units with stable runtime keys."""
    categories = menu_spec.get("categories", {})
    work_units: list[dict[str, Any]] = []

    for category_name, items in categories.items():
        if not isinstance(items, list):
            continue

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            unit = dict(item)
            unit["category"] = category_name
            unit[ITEM_KEY_FIELD] = f"{category_name}:{index}"
            work_units.append(unit)

    return work_units


def align_completed_items(
    menu_spec: dict[str, Any],
    completed_items: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach stable runtime keys to completed items by consuming menu order.

    This lets resume logic distinguish repeated names by matching persisted
    results back onto the ordered menu specification.
    """
    work_units = build_menu_work_units(menu_spec)
    available_by_label: dict[tuple[str, str], deque[str]] = defaultdict(deque)
    available_by_key = {
        str(unit[ITEM_KEY_FIELD]): dict(unit)
        for unit in work_units
    }

    for unit in work_units:
        label = (
            str(unit.get("category", "")),
            str(unit.get("name", "")),
        )
        available_by_label[label].append(str(unit[ITEM_KEY_FIELD]))

    aligned: list[dict[str, Any]] = []
    for item in completed_items:
        item_copy = dict(item)
        item_key = item_copy.get(ITEM_KEY_FIELD)

        if isinstance(item_key, str) and item_key in available_by_key:
            label = (
                str(available_by_key[item_key].get("category", "")),
                str(available_by_key[item_key].get("name", "")),
            )
            keys = available_by_label.get(label)
            if keys and item_key in keys:
                keys.remove(item_key)
        else:
            label = (
                str(item_copy.get("category", "")),
                str(item_copy.get("item_name", "")),
            )
            keys = available_by_label.get(label)
            if keys:
                item_key = keys.popleft()
                item_copy[ITEM_KEY_FIELD] = item_key

        aligned.append(item_copy)

    return aligned


def completed_item_keys(
    menu_spec: dict[str, Any],
    completed_items: Iterable[dict[str, Any]],
) -> set[str]:
    """Return completed work-unit keys for routing and worker selection."""
    return {
        str(item[ITEM_KEY_FIELD])
        for item in align_completed_items(menu_spec, completed_items)
        if item.get(ITEM_KEY_FIELD)
    }
