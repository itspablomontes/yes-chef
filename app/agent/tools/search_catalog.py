"""Tool: search_catalog — fuzzy search the Sysco catalog.

The LLM calls this tool to find ingredient matches. Returns
candidates with confidence scores; the LLM picks the best match
or rejects all if none fit (ADR-003).
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.infrastructure.catalog_index import build_catalog_index


@tool
def search_catalog(query: str, max_results: int = 5) -> str:
    """Search the Sysco supplier catalog for an ingredient.

    Args:
        query: Ingredient name to search for (e.g., "applewood smoked bacon")
        max_results: Maximum number of results to return (default 5)

    Returns:
        Formatted string with matching catalog items, scores, and pricing.
        If no matches found, returns "No matches found" message.
    """
    index = build_catalog_index()
    results = index.search(query, max_results=max_results)

    if not results:
        return (
            f"No matches found for '{query}' in the Sysco catalog. "
            f"This ingredient may not be available — mark as 'not_available' or 'estimated'."
        )

    lines = [f"Found {len(results)} match(es) for '{query}':\n"]
    for i, match in enumerate(results, 1):
        lines.append(
            f"{i}. {match.description}\n"
            f"   Sysco Item #: {match.item_number}\n"
            f"   Brand: {match.brand}\n"
            f"   Unit of Measure: {match.unit_of_measure}\n"
            f"   Case Cost: ${match.cost_per_case:.2f}\n"
            f"   Match Score: {match.score:.0f}/100\n"
        )

    return "\n".join(lines)
