"""Structured tool payloads for catalog search and pricing."""

from __future__ import annotations

from pydantic import BaseModel


class SearchCatalogMatchPayload(BaseModel):
    item_number: str
    description: str
    brand: str
    unit_of_measure: str
    cost_per_case: float
    score: float


class SearchCatalogResultPayload(BaseModel):
    query: str
    matches: list[SearchCatalogMatchPayload]


class ItemPriceCalculationPayload(BaseModel):
    case_cost: float
    case_uom: str
    total_case_quantity: str


class ItemPriceResultPayload(BaseModel):
    sysco_item_number: str
    description: str
    quantity_needed: str
    unit_cost: float | None
    calculation: ItemPriceCalculationPayload
