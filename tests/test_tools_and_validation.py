from __future__ import annotations

import importlib
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.agent.validation.schema_repair import repair_line_item

from app.agent.nodes.catalog_resolver import CatalogResolverNode
from app.agent.nodes.global_catalog_cache import GlobalCatalogCache
from app.agent.nodes.price_computer import PriceComputerNode, _price_cache_key
from app.infrastructure.catalog_index import CatalogMatch


@dataclass
class FakeCatalogEntry:
    item_number: str
    description: str
    brand: str
    unit_of_measure: str
    cost_per_case: float
    normalized: str = ""


class FakeCatalogIndex:
    def search(self, query: str, max_results: int = 5) -> list[CatalogMatch]:
        assert query == "sea scallops"
        assert max_results == 5
        return [
            CatalogMatch(
                item_number="7067228",
                description="SCALLOP, SEA, DRY PACK, U/10, IQF",
                brand="SYS IMP",
                unit_of_measure="20/8 OZ",
                cost_per_case=315.80,
                score=92.0,
            )
        ]

    def get_by_item_number(self, item_number: str) -> FakeCatalogEntry | None:
        assert item_number == "7067228"
        return FakeCatalogEntry(
            item_number="7067228",
            description="SCALLOP, SEA, DRY PACK, U/10, IQF",
            brand="SYS IMP",
            unit_of_measure="20/8 OZ",
            cost_per_case=315.80,
        )


def test_price_cache_key_format() -> None:
    """Price cache uses sysco_id::quantity format for deduplication."""
    key = _price_cache_key("7067228", "1 each")
    assert key == "7067228::1 each"
    assert isinstance(key, str)


def test_repair_fixes_null_ingredient_cost_from_sum() -> None:
    line = {
        "ingredient_cost_per_unit": None,
        "ingredients": [
            {"name": "a", "quantity": "1 oz", "unit_cost": 1.0, "source": "sysco_catalog"},
            {"name": "b", "quantity": "2 oz", "unit_cost": 2.0, "source": "sysco_catalog"},
        ],
    }
    repaired = repair_line_item(line)
    assert repaired["ingredient_cost_per_unit"] == 3.0


def test_catalog_resolver_batches_lookups_and_reuses_cache() -> None:
    def fake_invoke(args: dict) -> dict:
        return {"query": args["query"], "matches": [{"item_number": "123", "description": args["query"]}]}

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_tool:
        mock_tool.invoke = fake_invoke
        resolver = CatalogResolverNode()
        planned = [
            {"name": "applewood smoked bacon", "quantity_needed": "1 strip", "needs_catalog_lookup": True},
            {"name": "bacon", "quantity_needed": "0.5 oz", "needs_catalog_lookup": True},
        ]
        update = resolver.resolve(planned, cache={})
        assert update["catalog_lookups"] <= 2
        assert "resolved_ingredients" in update


def test_global_cache_deduplicates_by_normalized_name() -> None:
    def fake_invoke(args: dict) -> dict:
        return {"query": args["query"], "matches": [{"item_number": "123", "description": args["query"]}]}

    with patch("app.agent.nodes.global_catalog_cache.search_catalog") as mock_tool:
        mock_tool.invoke = fake_invoke
        cache = GlobalCatalogCache()
        cache.resolve_batch(["applewood smoked bacon", "bacon", "BACON"])
        assert cache.resolve_count <= 2


def test_price_computer_uses_cache_and_computes_total(monkeypatch: pytest.MonkeyPatch) -> None:
    get_item_price_module = importlib.import_module("app.agent.tools.get_item_price")
    monkeypatch.setattr(
        get_item_price_module,
        "build_catalog_index",
        lambda: FakeCatalogIndex(),
    )

    node = PriceComputerNode()
    resolved = [
        {
            "name": "bacon",
            "sysco_item_number": "7067228",
            "quantity_needed": "2 each",
            "source": "sysco_catalog",
        }
    ]
    update = node.compute(resolved, price_cache={})
    assert update["ingredient_cost_per_unit"] is not None
    assert len(update["priced_ingredients"]) == 1
    assert update["priced_ingredients"][0]["unit_cost"] is not None


def test_search_catalog_returns_structured_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    search_catalog_tool_module = importlib.import_module("app.agent.tools.search_catalog")

    monkeypatch.setattr(
        search_catalog_tool_module,
        "build_catalog_index",
        lambda: FakeCatalogIndex(),
    )

    result = search_catalog_tool_module.search_catalog.invoke({"query": "sea scallops"})

    assert result == {
        "query": "sea scallops",
        "matches": [
            {
                "item_number": "7067228",
                "description": "SCALLOP, SEA, DRY PACK, U/10, IQF",
                "brand": "SYS IMP",
                "unit_of_measure": "20/8 OZ",
                "cost_per_case": 315.80,
                "score": 92.0,
            }
        ],
    }


def test_get_item_price_returns_structured_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    get_item_price_tool_module = importlib.import_module("app.agent.tools.get_item_price")

    monkeypatch.setattr(
        get_item_price_tool_module,
        "build_catalog_index",
        lambda: FakeCatalogIndex(),
    )

    result = get_item_price_tool_module.get_item_price.invoke(
        {"sysco_item_number": "7067228", "quantity_needed": "2 each"}
    )

    assert result == {
        "sysco_item_number": "7067228",
        "description": "SCALLOP, SEA, DRY PACK, U/10, IQF",
        "quantity_needed": "2 each",
        "unit_cost": 3.95,
        "calculation": {
            "case_cost": 315.80,
            "case_uom": "20/8 OZ",
            "total_case_quantity": "160.0 OZ",
        },
    }


def test_validate_quote_schema_accepts_valid_quote_and_rejects_invalid() -> None:
    from app.application.schema_validator import validate_quote_schema

    valid_quote = {
        "quote_id": "quote-1",
        "event": "Test Event",
        "generated_at": "2026-03-06T00:00:00Z",
        "line_items": [
            {
                "item_name": "Sea Scallops",
                "category": "appetizers",
                "ingredients": [
                    {
                        "name": "Scallops",
                        "quantity": "2 each",
                        "unit_cost": 3.95,
                        "source": "sysco_catalog",
                        "sysco_item_number": "7067228",
                    }
                ],
                "ingredient_cost_per_unit": 3.95,
            }
        ],
    }

    invalid_quote = {
        "quote_id": "quote-2",
        "generated_at": "2026-03-06T00:00:00Z",
        "line_items": [],
    }

    validate_quote_schema(valid_quote)

    with pytest.raises(ValueError):
        validate_quote_schema(invalid_quote)
