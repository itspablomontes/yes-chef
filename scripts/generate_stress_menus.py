#!/usr/bin/env python3
"""Generate stress-test menu JSONs with 100, 250, and 500 items.

Uses items from data/menu_spec.json, cycling and varying names to reach target counts.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_MENU = Path(__file__).resolve().parent.parent / "data" / "menu_spec.json"
OUT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "stress-test"


def flatten_items(menu: dict) -> list[tuple[str, dict]]:
    """Return (category, item) pairs from menu."""
    items: list[tuple[str, dict]] = []
    for cat, entries in menu.get("categories", {}).items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    items.append((cat, dict(entry)))
    return items


def build_stress_menu(
    base_items: list[tuple[str, dict]],
    target_count: int,
    event_name: str,
    guest_count: int,
) -> dict:
    """Build a menu with target_count items by cycling base items with unique names."""
    categories: dict[str, list[dict]] = {
        "appetizers": [],
        "main_plates": [],
        "desserts": [],
        "cocktails": [],
    }
    # Distribute evenly across categories
    cat_names = list(categories.keys())
    for i in range(target_count):
        cat, item = base_items[i % len(base_items)]
        # Use target category for distribution; keep original if it fits
        target_cat = cat_names[i % len(cat_names)]
        copy = dict(item)
        copy["name"] = f"{item['name']} #{i + 1}"
        categories[target_cat].append(copy)

    return {
        "event": event_name,
        "date": "2026-03-06",
        "venue": "Stress Test Kitchen",
        "guest_count_estimate": guest_count,
        "notes": f"Stress test with {target_count} menu items.",
        "categories": categories,
    }


def main() -> None:
    base = json.loads(BASE_MENU.read_text(encoding="utf-8"))
    base_items = flatten_items(base)
    if not base_items:
        raise SystemExit("No items found in base menu")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    configs = [
        (100, "Stress Test 100 Items", 50),
        (250, "Stress Test 250 Items", 125),
        (500, "Stress Test 500 Items", 250),
    ]

    for count, event, guests in configs:
        menu = build_stress_menu(base_items, count, event, guests)
        out_path = OUT_DIR / f"menu_{count}_items.json"
        out_path.write_text(json.dumps(menu, indent=2), encoding="utf-8")
        print(f"Wrote {out_path} ({count} items)")


if __name__ == "__main__":
    main()
