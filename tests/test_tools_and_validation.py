from __future__ import annotations

from dataclasses import dataclass
import importlib

import pytest

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
