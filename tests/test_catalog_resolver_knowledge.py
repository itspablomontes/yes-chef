"""Unit tests for CatalogResolverNode carry-forward learnings (knowledge_store)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.agent.nodes.catalog_resolver import CatalogResolverNode
from app.infrastructure.catalog_index import normalize_query


@dataclass
class FakeCatalogEntry:
    """Minimal entry for get_by_item_number."""

    item_number: str
    description: str
    brand: str
    unit_of_measure: str
    cost_per_case: float
    normalized: str = ""


class FakeCatalogIndex:
    """Catalog index that returns known entries for get_by_item_number."""

    def get_by_item_number(self, item_number: str) -> FakeCatalogEntry | None:
        if item_number == "7067228":
            return FakeCatalogEntry(
                item_number="7067228",
                description="SCALLOP, SEA, DRY PACK, U/10, IQF",
                brand="SYS IMP",
                unit_of_measure="20/8 OZ",
                cost_per_case=315.80,
            )
        return None


def test_resolver_uses_knowledge_not_available_skips_lookup() -> None:
    """When knowledge_store has not_available, resolver returns it without catalog lookup."""
    planned = [
        {"name": "wagyu beef", "quantity_needed": "8 oz", "needs_catalog_lookup": True},
    ]
    knowledge = {normalize_query("wagyu beef"): "not_available"}

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_search:
        mock_search.invoke = lambda _: {"matches": []}
        resolver = CatalogResolverNode()
        result = resolver.resolve(planned, cache={}, knowledge_store=knowledge)

    assert result["catalog_lookups"] == 0
    ing = result["resolved_ingredients"][0]
    assert ing["source"] == "not_available"
    assert ing["name"] == "wagyu beef"
    assert ing["sysco_item_number"] is None


def test_resolver_uses_knowledge_estimated_skips_lookup() -> None:
    """When knowledge_store has estimated, resolver returns it without catalog lookup."""
    planned = [
        {"name": "truffle oil", "quantity_needed": "0.5 tbsp", "needs_catalog_lookup": True},
    ]
    knowledge = {normalize_query("truffle oil"): "estimated"}

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_search:
        mock_search.invoke = lambda _: {"matches": []}
        resolver = CatalogResolverNode()
        result = resolver.resolve(planned, cache={}, knowledge_store=knowledge)

    assert result["catalog_lookups"] == 0
    ing = result["resolved_ingredients"][0]
    assert ing["source"] == "estimated"
    assert ing["name"] == "truffle oil"


def test_resolver_uses_knowledge_found_skips_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When knowledge_store has found:<sysco_id>, resolver uses get_by_item_number, no search."""
    planned = [
        {"name": "sea scallops", "quantity_needed": "2 each", "needs_catalog_lookup": True},
    ]
    knowledge = {normalize_query("sea scallops"): "found:7067228"}

    monkeypatch.setattr(
        "app.agent.nodes.catalog_resolver.build_catalog_index",
        lambda: FakeCatalogIndex(),
    )

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_search:
        resolver = CatalogResolverNode()
        result = resolver.resolve(planned, cache={}, knowledge_store=knowledge)

        mock_search.invoke.assert_not_called()

    assert result["catalog_lookups"] == 0
    ing = result["resolved_ingredients"][0]
    assert ing["source"] == "sysco_catalog"
    assert ing["sysco_item_number"] == "7067228"


def test_resolver_mixes_knowledge_and_lookup() -> None:
    """Some ingredients from knowledge, some require catalog lookup."""
    planned = [
        {"name": "wagyu beef", "quantity_needed": "8 oz", "needs_catalog_lookup": True},
        {"name": "bacon", "quantity_needed": "1 strip", "needs_catalog_lookup": True},
    ]
    knowledge = {normalize_query("wagyu beef"): "not_available"}

    def fake_invoke(args: dict) -> dict:
        return {"query": args["query"], "matches": [{"item_number": "4842788", "description": "BACON"}]}

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_search:
        mock_search.invoke = fake_invoke
        resolver = CatalogResolverNode()
        result = resolver.resolve(planned, cache={}, knowledge_store=knowledge)

    assert result["catalog_lookups"] == 1
    wagyu = next(i for i in result["resolved_ingredients"] if "wagyu" in i["name"].lower())
    bacon = next(i for i in result["resolved_ingredients"] if "bacon" in i["name"].lower())
    assert wagyu["source"] == "not_available"
    assert bacon["source"] == "sysco_catalog"
