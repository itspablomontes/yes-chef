"""Stats stream adapter — consumes estimation service events and yields stats-only SSE.

Provides a simplified stream for clients that want progress stats without parsing
raw item/tool events. Used by POST /estimate/stream and POST /estimate/{id}/resume/stream.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any


def _count_total_items(menu_spec: dict[str, Any]) -> int:
    categories = menu_spec.get("categories", {})
    if not isinstance(categories, dict):
        return 0
    return sum(len(items) for items in categories.values() if isinstance(items, list))


def _build_stats_payload(
    *,
    estimation_id: str,
    start_time: float,
    items_completed: int,
    total_items: int,
    last_item_name: str,
    current_activity: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    errors_count: int,
    status: str,
    quote_received: bool,
) -> dict[str, Any]:
    elapsed = time.monotonic() - start_time
    return {
        "estimation_id": estimation_id,
        "elapsed_seconds": round(elapsed, 2),
        "items_completed": items_completed,
        "total_items": total_items,
        "last_item_name": last_item_name,
        "current_activity": current_activity,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "errors_count": errors_count,
        "status": status,
        "quote_received": quote_received,
    }


async def stats_stream(
    event_gen: AsyncGenerator[dict[str, Any], None],
    total_items: int,
    estimation_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Consume estimation events and yield stats-only events.

    Args:
        event_gen: Async generator from EstimationService.create_estimation or resume_estimation
        total_items: Total menu items (from menu_spec or job)
        estimation_id: Pre-known id for resume; otherwise captured from first event
    """
    start_time = time.monotonic()
    items_completed = 0
    last_item_name = "None yet"
    current_activity = "Waiting for events"
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    errors_count = 0
    quote_received = False
    status = "in_progress"
    final_quote: dict[str, Any] | None = None

    async for event in event_gen:
        event_type = str(event.get("event", ""))
        raw_data = event.get("data", {})
        data = raw_data if isinstance(raw_data, dict) else {}

        eid = str(
            event.get("estimation_id") or data.get("estimation_id") or estimation_id or ""
        )
        if eid:
            estimation_id = eid

        if event_type == "estimation_started":
            current_activity = "Initializing estimation"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "item_started":
            item_name = str(data.get("item_name", "Unknown Item"))
            last_item_name = item_name
            current_activity = f"Starting item: {item_name}"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "llm_waiting":
            current_activity = str(data.get("message", "Waiting for LLM response"))
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "tool_started":
            tool_name = str(data.get("tool", "Unknown tool"))
            current_activity = f"Running tool: {tool_name}"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "tool_waiting":
            tool_name = str(data.get("tool", "Unknown tool"))
            elapsed_s = data.get("elapsed_seconds", "?")
            current_activity = f"{tool_name} still running ({elapsed_s}s)"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "tool_finished":
            tool_name = str(data.get("tool", "Unknown tool"))
            if data.get("status") == "error":
                errors_count += 1
                current_activity = f"{tool_name} failed"
            else:
                current_activity = f"{tool_name} finished"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "validation_retry":
            attempt = data.get("attempt", "?")
            current_activity = f"Retrying item validation (attempt {attempt})"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "item_complete":
            items_completed += 1
            item_name = str(data.get("item_name", last_item_name))
            last_item_name = item_name
            current_activity = f"Completed item: {item_name}"
            telemetry = data.get("telemetry")
            if isinstance(telemetry, dict):
                prompt_tokens += int(telemetry.get("prompt_tokens", 0))
                completion_tokens += int(telemetry.get("completion_tokens", 0))
                total_tokens += int(telemetry.get("total_tokens", 0))
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "estimation_metrics":
            prompt_tokens = int(data.get("prompt_tokens", prompt_tokens))
            completion_tokens = int(data.get("completion_tokens", completion_tokens))
            total_tokens = int(data.get("total_tokens", total_tokens))
            current_activity = f"Run metrics ready (tokens: {total_tokens})"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "quote_complete":
            quote_received = True
            final_quote = data if isinstance(data, dict) and data else None
            current_activity = "Finalizing quote"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            continue

        if event_type == "estimation_complete":
            status = str(data.get("status", "completed"))
            current_activity = f"Completed ({status})"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status=status,
                    quote_received=quote_received,
                ),
            }
            if final_quote is not None:
                yield {"event": "quote_complete", "data": final_quote}
            continue

        if event_type == "error":
            errors_count += 1
            current_activity = "Run failed"
            yield {
                "event": "stats",
                "data": _build_stats_payload(
                    estimation_id=estimation_id or "",
                    start_time=start_time,
                    items_completed=items_completed,
                    total_items=total_items,
                    last_item_name=last_item_name,
                    current_activity=current_activity,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    errors_count=errors_count,
                    status="error",
                    quote_received=quote_received,
                ),
            }
            yield {"event": "error", "data": data}
            continue
