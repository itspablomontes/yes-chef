"""Pydantic request/response schemas for the HTTP API.

These are presentation-layer DTOs — NOT domain entities.
They define the contract between the client and the server.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EstimationRequest(BaseModel):
    """Request body for POST /estimate.

    Accepts the raw menu_spec.json format directly.
    """

    event: str = Field(description="Event name")
    date: str = Field(description="Event date")
    venue: str = Field(description="Venue name")
    guest_count_estimate: int = Field(description="Estimated guest count")
    notes: str = Field(default="", description="Additional notes")
    categories: dict[str, list[dict[str, Any]]] = Field(
        description="Menu categories with items"
    )


class EstimationStatusResponse(BaseModel):
    """Response for GET /estimate/{id}."""

    id: str
    event_name: str
    total_items: int
    items_completed: int
    status: str
    created_at: str
    updated_at: str
    quote: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: str = "ok"
    version: str = "0.1.0"
