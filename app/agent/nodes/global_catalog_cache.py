"""Global catalog cache — deduplicates lookups by normalized ingredient name."""

from __future__ import annotations

from typing import Any

from app.agent.tools.search_catalog import search_catalog
from app.infrastructure.catalog_index import normalize_query


class GlobalCatalogCache:
    """Cache for catalog resolution across menu items. Deduplicates by normalized name."""

    def __init__(self, cache: dict[str, dict[str, Any]] | None = None) -> None:
        self._cache: dict[str, dict[str, Any]] = dict(cache) if cache else {}
        self._resolve_count = 0

    @property
    def resolve_count(self) -> int:
        """Number of actual catalog lookups performed."""
        return self._resolve_count

    def _normalize(self, name: str) -> str:
        return normalize_query(name)

    def resolve_batch(
        self,
        ingredient_names: list[str],
        max_results: int = 3,
    ) -> dict[str, dict[str, Any]]:
        """Resolve a batch of ingredient names. Deduplicates by normalized name."""
        seen: set[str] = set()
        results: dict[str, dict[str, Any]] = {}

        for name in ingredient_names:
            key = self._normalize(name)
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)

            if key in self._cache:
                results[key] = self._cache[key]
                continue

            self._resolve_count += 1
            raw = search_catalog.invoke({"query": name, "max_results": max_results})
            self._cache[key] = raw
            results[key] = raw

        return results

    def get(self, normalized_key: str) -> dict[str, Any] | None:
        """Get cached result by normalized key."""
        return self._cache.get(normalized_key)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Export cache for serialization (e.g. state persistence)."""
        return dict(self._cache)
