"""Estimation orchestrator — wraps graph execution with event dispatch.

Bridges the compiled graph and the observer pattern by intercepting
graph events (via astream_events) and dispatching to registered observers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.agent.state import EstimationState
from app.application.progress_observer import EstimationObserver
from app.application.runtime.event_contract_validator import EventContractValidator
from app.application.schema_validator import validate_quote_schema
from app.application.stream_events import (
    EstimationProgressEvent,
    bind_progress_event_sink,
)
from app.application.work_units import ITEM_KEY_FIELD

logger = logging.getLogger(__name__)


class EstimationOrchestrator:
    """Orchestrates a graph run and dispatches events to observers.

    The orchestrator does NOT contain business logic — it's plumbing
    that connects the graph execution to the observer pipeline.
    """

    def __init__(self, graph: CompiledStateGraph) -> None:
        self._graph = graph
        self._observers: list[EstimationObserver] = []
        self._event_validator = EventContractValidator()

    def add_observer(self, observer: EstimationObserver) -> None:
        """Register an observer for estimation events."""
        self._observers.append(observer)

    def remove_observer(self, observer: EstimationObserver) -> None:
        """Unregister an observer."""
        self._observers.remove(observer)

    async def _enqueue_graph_events(
        self,
        estimation_id: str,
        state: EstimationState,
        event_queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        """Run the graph in the background and enqueue runtime events."""

        async def sink(event: EstimationProgressEvent) -> None:
            await event_queue.put(("progress", event))

        try:
            async with bind_progress_event_sink(sink):
                async for mode, output in self._graph.astream(
                    state.model_dump(),
                    config={"configurable": {"thread_id": estimation_id}},
                    stream_mode=["updates", "messages"],
                ):
                    await event_queue.put(("graph", (mode, output)))
        except Exception as exc:
            await event_queue.put(("exception", exc))
        finally:
            await event_queue.put(("done", None))

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
            "data": {"estimation_id": estimation_id},
        }

        try:
            # Build EstimationState from initial state dict
            state = EstimationState(**initial_state)

            # Track items we've already notified about
            seen_items: set[str] = set()
            quote_emitted = False
            final_status = str(state.status)
            telemetry_totals: dict[str, float] = {
                "llm_calls": 0,
                "tool_calls": 0,
                "rate_limit_retries": 0,
                "validation_retries": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "duration_seconds": 0.0,
            }

            event_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            graph_task = asyncio.create_task(
                self._enqueue_graph_events(estimation_id, state, event_queue)
            )

            try:
                while True:
                    event_kind, payload = await event_queue.get()

                    if event_kind == "progress":
                        progress_event = payload
                        if isinstance(progress_event, EstimationProgressEvent):
                            yield {
                                "event": progress_event.event,
                                "data": progress_event.data,
                            }
                        continue

                    if event_kind == "exception":
                        raise payload

                    if event_kind == "done":
                        break

                    if event_kind != "graph":
                        continue

                    mode, output = payload
                    if mode == "messages" and isinstance(output, tuple) and len(output) == 2:
                        continue

                    if mode == "updates" and isinstance(output, dict):
                        for _node_name, node_output in output.items():
                            if not isinstance(node_output, dict):
                                continue

                            completed_items = node_output.get("completed_items", [])
                            if isinstance(completed_items, list):
                                # Client disconnect during yield must not prevent persistence.
                                items_to_emit: list[dict[str, Any]] = []
                                for item in completed_items:
                                    if not isinstance(item, dict):
                                        continue
                                    item_key = str(item.get(ITEM_KEY_FIELD, ""))
                                    item_identity = item_key or str(item.get("item_name", ""))
                                    if item_identity and item_identity not in seen_items:
                                        seen_items.add(item_identity)
                                        telemetry = item.get("telemetry")
                                        if isinstance(telemetry, dict):
                                            telemetry_totals["llm_calls"] += float(telemetry.get("llm_calls", 0))
                                            telemetry_totals["tool_calls"] += float(telemetry.get("tool_calls", 0))
                                            telemetry_totals["rate_limit_retries"] += float(
                                                telemetry.get("rate_limit_retries", 0)
                                            )
                                            telemetry_totals["validation_retries"] += float(
                                                telemetry.get("validation_retries", 0)
                                            )
                                            telemetry_totals["prompt_tokens"] += float(
                                                telemetry.get("prompt_tokens", 0)
                                            )
                                            telemetry_totals["completion_tokens"] += float(
                                                telemetry.get("completion_tokens", 0)
                                            )
                                            telemetry_totals["total_tokens"] += float(
                                                telemetry.get("total_tokens", 0)
                                            )
                                            telemetry_totals["duration_seconds"] += float(
                                                telemetry.get("duration_seconds", 0.0)
                                            )

                                        for observer in self._observers:
                                            await observer.on_item_complete(estimation_id, item)
                                        items_to_emit.append(item)

                                for item in items_to_emit:
                                    event_payload = {"event": "item_complete", "data": item}
                                    self._event_validator.validate(event_payload)
                                    yield event_payload

                            quote = node_output.get("quote", {})
                            node_status = node_output.get("status")
                            if isinstance(node_status, str) and node_status:
                                final_status = node_status

                            if isinstance(quote, dict) and quote and not quote_emitted:
                                validate_quote_schema(quote)
                                quote_emitted = True
                                for observer in self._observers:
                                    await observer.on_estimation_complete(estimation_id, quote)

                                event_payload = {"event": "quote_complete", "data": quote}
                                self._event_validator.validate(event_payload)
                                yield event_payload
            finally:
                if not graph_task.done():
                    graph_task.cancel()
                with suppress(asyncio.CancelledError):
                    await graph_task
                    
            yield {
                "event": "estimation_metrics",
                "data": {
                    "items_processed": len(seen_items),
                    "llm_calls": int(telemetry_totals["llm_calls"]),
                    "tool_calls": int(telemetry_totals["tool_calls"]),
                    "rate_limit_retries": int(telemetry_totals["rate_limit_retries"]),
                    "validation_retries": int(telemetry_totals["validation_retries"]),
                    "prompt_tokens": int(telemetry_totals["prompt_tokens"]),
                    "completion_tokens": int(telemetry_totals["completion_tokens"]),
                    "total_tokens": int(telemetry_totals["total_tokens"]),
                    "duration_seconds": round(float(telemetry_totals["duration_seconds"]), 2),
                },
            }

            event_payload = {
                "event": "estimation_complete",
                "data": {
                    "status": final_status or "completed",
                    "items_processed": len(seen_items),
                },
            }
            self._event_validator.validate(event_payload)
            yield event_payload

        except Exception as e:
            error_msg = str(e)
            logger.error("Estimation %s failed: %s", estimation_id, error_msg)

            for observer in self._observers:
                await observer.on_error(estimation_id, error_msg)

            event_payload = {"event": "error", "data": {"message": error_msg}}
            self._event_validator.validate(event_payload)
            yield event_payload
