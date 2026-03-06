"""Deterministic batch catalog resolver — resolves planned ingredients using global cache."""

from __future__ import annotations

from typing import Any

from app.agent.nodes.global_catalog_cache import GlobalCatalogCache
from app.infrastructure.catalog_index import build_catalog_index, normalize_query


def _build_match_from_item_number(item_number: str) -> dict[str, Any] | None:
    """Build a match dict from CatalogIndex for a known Sysco item number.

    Used when KnowledgeStore has found:<sysco_id> — we skip search and
    reconstruct the match from the catalog for pricing.
    """
    index = build_catalog_index()
    entry = index.get_by_item_number(item_number)
    if entry is None:
        return None
    return {
        "item_number": entry.item_number,
        "description": entry.description,
        "brand": entry.brand,
        "unit_of_measure": entry.unit_of_measure,
        "cost_per_case": entry.cost_per_case,
        "score": 100.0,
    }


class CatalogResolverNode:
    """Resolves planned ingredients to catalog matches. Uses GlobalCatalogCache for deduplication."""

    def __init__(self, cache: GlobalCatalogCache | None = None) -> None:
        self._cache = cache or GlobalCatalogCache()

    def resolve(
        self,
        planned: list[dict[str, Any]],
        cache: GlobalCatalogCache | dict[str, dict[str, Any]] | None = None,
        max_results: int = 3,
        knowledge_store: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Resolve a batch of planned ingredients. Returns resolved list and cache stats.

        If knowledge_store is provided, pre-resolve ingredients known as not_available,
        estimated, or found:<sysco_id> without calling the catalog. This implements
        carry-forward learnings: on resume, we skip re-discovering known failures.
        """
        if isinstance(cache, dict):
            use_cache = GlobalCatalogCache(cache=cache)
        else:
            use_cache = cache or self._cache
        knowledge = knowledge_store or {}

        # Pre-resolve from knowledge store (carry-forward learnings)
        resolved_map: dict[str, dict[str, Any]] = {}
        for p in planned:
            if not p.get("needs_catalog_lookup", True) or not p.get("name"):
                continue
            name = p.get("name", "")
            key = normalize_query(name)
            status = knowledge.get(key)

            if status == "not_available":
                resolved_map[key] = {"matches": []}
            elif status == "estimated":
                resolved_map[key] = {"matches": [], "_source": "estimated"}
            elif status and status.startswith("found:"):
                sysco_id = status.split(":", 1)[1]
                match = _build_match_from_item_number(sysco_id)
                resolved_map[key] = {"matches": [match]} if match else {"matches": []}

        names_to_resolve = [
            p["name"]
            for p in planned
            if p.get("needs_catalog_lookup", True) and p.get("name")
        ]
        names_to_resolve = [
            n for n in names_to_resolve if normalize_query(n) not in resolved_map
        ]

        cache_results = use_cache.resolve_batch(names_to_resolve, max_results=max_results)
        resolved_map.update(cache_results)

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

            if not match_data:
                resolved_ingredients.append({
                    "name": name,
                    "quantity_needed": quantity,
                    "source": "not_available",
                    "sysco_item_number": None,
                    "unit_cost": None,
                })
                continue

            if match_data.get("_source") == "estimated":
                resolved_ingredients.append({
                    "name": name,
                    "quantity_needed": quantity,
                    "source": "estimated",
                    "sysco_item_number": None,
                    "unit_cost": None,
                })
                continue

            if not match_data.get("matches"):
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
