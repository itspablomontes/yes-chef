"""System prompt builder for the item worker and planning stage."""

from __future__ import annotations

from typing import Any

from app.agent.prompts.planning_prompt import (
    PLANNING_PROMPT_TEMPLATE,
    build_planning_context,
)

SYSTEM_PROMPT_TEMPLATE = """You are a culinary cost estimator for a high-end catering company (Elegant Foods).
Your job is to decompose a menu item into its component ingredients, find each ingredient in the
Sysco supplier catalog, and calculate the per-serving ingredient cost.

## Rules & Constraints

1. Process exactly ONE menu item per invocation.
2. Use search_catalog to find ingredients — do NOT guess prices.
3. Use get_item_price for cost calculation — do NOT do math yourself.
4. If an ingredient is not in the Sysco catalog after searching, mark source as "not_available" with unit_cost: null.
5. If you can reasonably estimate a missing item's price, mark source as "estimated" and provide your best estimate.
6. Include ALL ingredients, even garnishes, oils, and seasonings.
7. Quantities should be per serving (per piece, per plate, per serving, or per drink).
8. Consider the service_style: "passed" items are smaller portions than plated items.
9. After processing all ingredients, call save_item_result with the complete result.

## Output Contract

Your final save_item_result call must include:
- item_name: exact name from the menu spec
- category: appetizers, main_plates, desserts, or cocktails
- ingredients: list of objects, each with:
  - name: ingredient name
  - quantity: amount per serving (e.g., "8 oz", "2 each", "0.5 tbsp")
  - unit_cost: cost for that quantity (float or null)
  - source: "sysco_catalog" | "estimated" | "not_available"
  - sysco_item_number: Sysco item # if matched, null otherwise
- ingredient_cost_per_unit: sum of all non-null unit_costs

{knowledge_hints}

{item_context}"""


def build_knowledge_hints(knowledge: dict[str, str]) -> str:
    """Convert KnowledgeStore dict to a prompt section."""
    if not knowledge:
        return ""

    lines = ["## Known Catalog Findings (from previous items):\n"]
    for ingredient, status in knowledge.items():
        if status == "not_available":
            lines.append(f"- {ingredient}: NOT AVAILABLE in Sysco catalog. Mark as not_available immediately.")
        elif status == "estimated":
            lines.append(f"- {ingredient}: Not in catalog, use estimated pricing.")
        elif status.startswith("found:"):
            sysco_id = status.split(":", 1)[1]
            lines.append(f"- {ingredient}: FOUND in catalog → Sysco #{sysco_id}. Use get_item_price directly.")
    return "\n".join(lines)


def build_item_context(menu_item: dict[str, Any], category: str) -> str:
    """Format menu item details for prompt injection."""
    name = menu_item.get("name", "Unknown")
    description = menu_item.get("description", "")
    dietary_notes = menu_item.get("dietary_notes", "")
    service_style = menu_item.get("service_style", "plated")

    return (
        f"## Your Current Item:\n\n"
        f"Name: {name}\n"
        f"Description: {description}\n"
        f"Category: {category}\n"
        f"Dietary Notes: {dietary_notes or 'None'}\n"
        f"Service Style: {service_style or 'plated'}"
    )


def build_system_prompt(
    menu_item: dict[str, Any],
    category: str,
    knowledge: dict[str, str],
) -> str:
    """Assemble the complete system prompt for an item worker."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        knowledge_hints=build_knowledge_hints(knowledge),
        item_context=build_item_context(menu_item, category),
    )
