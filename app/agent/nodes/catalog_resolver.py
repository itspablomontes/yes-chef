"""Deterministic batch catalog resolver — resolves planned ingredients using global cache."""

from __future__ import annotations

from typing import Any

from app.agent.nodes.global_catalog_cache import GlobalCatalogCache
from app.infrastructure.catalog_index import normalize_query


class CatalogResolverNode:
    """Resolves planned ingredients to catalog matches. Uses GlobalCatalogCache for deduplication."""

    def __init__(self, cache: GlobalCatalogCache | None = None) -> None:
        self._cache = cache or GlobalCatalogCache()

    def resolve(
        self,
        planned: list[dict[str, Any]],
        cache: GlobalCatalogCache | dict[str, dict[str, Any]] | None = None,
        max_results: int = 3,
    ) -> dict[str, Any]:
        """Resolve a batch of planned ingredients. Returns resolved list and cache stats."""
        if isinstance(cache, dict):
            use_cache = GlobalCatalogCache(cache=cache)
        else:
            use_cache = cache or self._cache
        names_to_resolve = [
            p["name"]
            for p in planned
            if p.get("needs_catalog_lookup", True) and p.get("name")
        ]

        resolved_map = use_cache.resolve_batch(names_to_resolve, max_results=max_results)

        resolved_ingredients: list[dict[str, Any]] = []
        for p in planned:
            name = p.get("name", "")
            quantity = p.get("quantity_needed", "")
            if not p.get("needs_catalog_lookup", True):
                resolved_ingredients.append({
                    "name": name,
                    "quantity_needed": quantity,
                    "source": "estimated",
                    "sysco_item_number": None,
                    "unit_cost": None,
                })
                continue

            key = normalize_query(name)
            match_data = resolved_map.get(key)

            if not match_data or not match_data.get("matches"):
                resolved_ingredients.append({
                    "name": name,
                    "quantity_needed": quantity,
                    "source": "not_available",
                    "sysco_item_number": None,
                    "unit_cost": None,
                })
                continue

            best = match_data["matches"][0]
            resolved_ingredients.append({
                "name": name,
                "quantity_needed": quantity,
                "source": "sysco_catalog",
                "sysco_item_number": best.get("item_number"),
                "unit_cost": None,
                "_match": best,
            })

        return {
            "resolved_ingredients": resolved_ingredients,
            "catalog_lookups": use_cache.resolve_count,
            "item_stage": "catalog_resolution",
            "cache": use_cache.to_dict(),
        }
