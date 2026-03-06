"""Knowledge store — carry-forward learnings across item workers.

A shared dictionary that accumulates findings from completed items
(e.g., catalog misses, successful matches). Each new worker receives
a snapshot of the current knowledge at dispatch time.

On resume, the knowledge store is reconstructed from persisted results.

Keys use normalize_query() so they match catalog resolver lookups.
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.catalog_index import normalize_query


class KnowledgeStore:
    """Carry-forward knowledge across item processing.

    Stores ingredient → status mappings to avoid redundant LLM lookups:
    - "not_available": ingredient not in catalog
    - "estimated": ingredient priced via estimation
    - "found:<sysco_id>": ingredient found with specific Sysco item number
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def update(self, ingredient: str, status: str) -> None:
        """Record a finding for an ingredient. Key uses normalize_query for catalog alignment."""
        key = normalize_query(ingredient)
        if not key:
            return
        self._store[key] = status

    def get_hints(self) -> dict[str, str]:
        """Get a snapshot of current knowledge for prompt injection."""
        return dict(self._store)

    def reconstruct_from_items(self, completed_items: list[dict[str, Any]]) -> None:
        """Rebuild knowledge store from persisted item results.

        Called on resume to restore carry-forward state from previously
        completed items.
        """
        for item in completed_items:
            ingredients = item.get("ingredients", [])
            for ing in ingredients:
                if not isinstance(ing, dict):
                    continue
                name = str(ing.get("name", ""))
                source = str(ing.get("source", ""))
                sysco_id = ing.get("sysco_item_number")

                if source == "sysco_catalog" and sysco_id:
                    self.update(name, f"found:{sysco_id}")
                elif source == "estimated":
                    self.update(name, "estimated")
                elif source == "not_available":
                    self.update(name, "not_available")

    @property
    def size(self) -> int:
        """Number of known ingredients."""
        return len(self._store)
