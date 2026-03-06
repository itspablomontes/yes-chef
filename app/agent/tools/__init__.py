"""Tool registry — single source of truth for all agent tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent.tools.get_item_price import get_item_price
from app.agent.tools.search_catalog import search_catalog


def get_all_tools() -> list[BaseTool]:
    """Return all tools available to the agent."""
    return [search_catalog, get_item_price]
