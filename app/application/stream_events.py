"""Shared progress event contract for estimation streaming.

Defines lightweight runtime events emitted during long-running estimation work.
These events are transient stream signals for UX feedback and are separate from
the durable persistence events handled by observers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class EstimationProgressEvent:
    """Internal progress event emitted during estimation runtime."""

    event: str
    data: dict[str, Any]


ProgressEventSink = Callable[[EstimationProgressEvent], Awaitable[None]]

_progress_event_sink: ContextVar[ProgressEventSink | None] = ContextVar(
    "progress_event_sink",
    default=None,
)


@asynccontextmanager
async def bind_progress_event_sink(
    sink: ProgressEventSink,
) -> AsyncIterator[None]:
    """Bind a per-request progress sink for nested runtime code."""

    token = _progress_event_sink.set(sink)
    try:
        yield
    finally:
        _progress_event_sink.reset(token)


async def emit_progress_event(event: str, **data: Any) -> None:
    """Emit a transient progress event when a sink is available."""

    sink = _progress_event_sink.get()
    if sink is None:
        return

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        **data,
    }
    await sink(EstimationProgressEvent(event=event, data=payload))
