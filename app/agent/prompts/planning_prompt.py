"""System prompt for the one-shot ingredient planning stage."""

from __future__ import annotations

from typing import Any

PLANNING_PROMPT_TEMPLATE = """You are a culinary cost estimator. Your ONLY job is to list the ingredients needed for ONE menu item.

Output a structured list of ingredients for this dish. For each ingredient:
- name: exact ingredient name (e.g., "diver scallops", "applewood smoked bacon")
- quantity_needed: amount per serving (e.g., "2 each", "8 oz", "1 strip")
- needs_catalog_lookup: true if we should search Sysco catalog; false for trivial items (salt, pepper, oil) that we estimate

Include ALL ingredients: proteins, garnishes, oils, seasonings.

{item_context}
{knowledge_hints}"""


def format_knowledge_hints(knowledge: dict[str, str]) -> str:
    """Format knowledge store for prompt injection.

    Returns human-readable lines for known catalog status. Keys are normalized
    (e.g. "beef wagyu"); we use them as-is for display.
    """
    if not knowledge:
        return ""
    lines: list[str] = []
    for key, status in sorted(knowledge.items()):
        if status == "not_available":
            lines.append(f"- {key}: not available")
        elif status == "estimated":
            lines.append(f"- {key}: estimated")
        elif status.startswith("found:"):
            lines.append(f"- {key}: found in Sysco")
    if not lines:
        return ""
    return (
        "\n\nKnown catalog status (from previous items):\n"
        + "\n".join(lines)
        + "\nFor not_available items, set needs_catalog_lookup: false."
    )


def build_planning_context(menu_item: dict[str, Any], category: str) -> str:
    """Format menu item for prompt."""
    name = menu_item.get("name", "Unknown")
    description = menu_item.get("description", "")
    dietary_notes = menu_item.get("dietary_notes", "")
    service_style = menu_item.get("service_style", "plated")
    return (
        f"## Menu Item:\n"
        f"Name: {name}\n"
        f"Description: {description}\n"
        f"Category: {category}\n"
        f"Dietary: {dietary_notes or 'None'}\n"
        f"Service Style: {service_style or 'plated'}"
    )
