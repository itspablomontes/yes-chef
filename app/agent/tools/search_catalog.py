"""Tool: search_catalog — fuzzy search the Sysco catalog.

The LLM calls this tool to find ingredient matches. Returns
candidates with confidence scores; the LLM picks the best match
or rejects all if none fit (ADR-003).
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.infrastructure.catalog_index import build_catalog_index
from app.agent.tools.schemas import SearchCatalogMatchPayload, SearchCatalogResultPayload


@tool
def search_catalog(query: str, max_results: int = 5) -> dict[str, object]:
    """Search the Sysco supplier catalog for an ingredient.

    Args:
        query: Ingredient name to search for (e.g., "applewood smoked bacon")
        max_results: Maximum number of results to return (default 5)

    Returns:
        Structured catalog candidates for the LLM to reason over.
    """
    index = build_catalog_index()
    results = index.search(query, max_results=max_results)
    payload = SearchCatalogResultPayload(
        query=query,
        matches=[
            SearchCatalogMatchPayload(
                item_number=match.item_number,
                description=match.description,
                brand=match.brand,
                unit_of_measure=match.unit_of_measure,
                cost_per_case=match.cost_per_case,
                score=match.score,
            )
            for match in results
        ],
    )
    return payload.model_dump()
