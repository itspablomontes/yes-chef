"""Estimation orchestrator — wraps graph execution with event dispatch.

Bridges the compiled graph and the observer pattern by intercepting
graph events (via astream_events) and dispatching to registered observers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.agent.state import EstimationState
from app.application.progress_observer import EstimationObserver

logger = logging.getLogger(__name__)


class EstimationOrchestrator:
    """Orchestrates a graph run and dispatches events to observers.

    The orchestrator does NOT contain business logic — it's plumbing
    that connects the graph execution to the observer pipeline.
    """

    def __init__(self, graph: CompiledStateGraph) -> None:
        self._graph = graph
        self._observers: list[EstimationObserver] = []

    def add_observer(self, observer: EstimationObserver) -> None:
        """Register an observer for estimation events."""
        self._observers.append(observer)

    def remove_observer(self, observer: EstimationObserver) -> None:
        """Unregister an observer."""
        self._observers.remove(observer)

    async def stream(
        self,
        estimation_id: str,
        initial_state: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute the graph and yield SSE-compatible events.

        Events yielded:
        - {"event": "estimation_started", "estimation_id": ...}
        - {"event": "item_complete", "data": {...item_data...}}
        - {"event": "quote_complete", "data": {...quote...}}
        - {"event": "error", "data": {"message": ...}}
        """
        yield {
            "event": "estimation_started",
            "estimation_id": estimation_id,
        }

        try:
            # Build EstimationState from initial state dict
            state = EstimationState(**initial_state)

            # Track items we've already notified about
            seen_items: set[str] = set()

            # Run the graph with streaming to yield batch updates sequentially
            async for mode, output in self._graph.astream(
                state.model_dump(),
                config={"configurable": {"thread_id": estimation_id}},
                stream_mode=["updates", "messages"]
            ):
                if mode == "messages" and isinstance(output, tuple) and len(output) == 2:
                    chunk, metadata = output
                    # Stream intermediate tool calls so the user sees real-time progress
                    if hasattr(chunk, "tool_call_chunks") and isinstance(
                        getattr(chunk, "tool_call_chunks", None), list
                    ):
                        for tc_chunk in chunk.tool_call_chunks:
                            # The "name" field is only present in the first chunk of a new tool call
                            if tc_chunk.get("name"):
                                tool_name = tc_chunk["name"]
                                if tool_name != "SaveBatchResult":
                                    yield {
                                        "event": "tool_call",
                                        "data": {"tool": tool_name}
                                    }
                    continue

                if mode == "updates" and isinstance(output, dict):
                    for _node_name, node_output in output.items():
                        if not isinstance(node_output, dict):
                            continue

                        # Process completed items from the current batch update
                        completed_items = node_output.get("completed_items", [])
                        if isinstance(completed_items, list):
                            for item in completed_items:
                                if not isinstance(item, dict):
                                    continue
                                item_name = item.get("item_name", "")
                                if item_name and item_name not in seen_items:
                                    seen_items.add(item_name)

                                    # Notify observers (e.g. persisting to database)
                                    for observer in self._observers:
                                        await observer.on_item_complete(estimation_id, item)

                                    yield {
                                        "event": "item_complete",
                                        "data": item,
                                    }

                        # Get the final quote from the reduce node update
                        quote = node_output.get("quote", {})
                        if quote:
                            for observer in self._observers:
                                await observer.on_estimation_complete(estimation_id, quote)

                            yield {
                                "event": "quote_complete",
                                "data": quote,
                            }
                    
            yield {
                "event": "estimation_complete",
                "data": {
                    "status": "completed",
                    "items_processed": len(seen_items),
                },
            }

        except Exception as e:
            error_msg = str(e)
            logger.error("Estimation %s failed: %s", estimation_id, error_msg)

            for observer in self._observers:
                await observer.on_error(estimation_id, error_msg)

            yield {
                "event": "error",
                "data": {"message": error_msg},
            }
