"""FastAPI routes for the estimation API.

POST /estimate              → Create new estimation (SSE stream)
POST /estimate/stream       → Create estimation, stream stats-only SSE
GET  /estimate/{id}         → Get estimation status
POST /estimate/{id}/resume  → Resume interrupted estimation (SSE stream)
POST /estimate/{id}/resume/stream → Resume estimation, stream stats-only SSE
GET  /health                → Health check
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.application.estimation_service import EstimationService
from app.presentation.dependencies import EstimationServiceDep
from app.presentation.schemas import (
    EstimationRequest,
    EstimationStatusResponse,
    HealthResponse,
)
from app.presentation.stats_stream import stats_stream

router = APIRouter()


async def _sse_generator(
    service: EstimationService,
    menu_spec: dict[str, object],
    estimation_id: str | None = None,
) -> StreamingResponse:
    """Create an SSE StreamingResponse from the estimation service."""

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            if estimation_id:
                gen = service.resume_estimation(estimation_id)
            else:
                gen = service.create_estimation(menu_spec)

            async for event in gen:
                event_type = event.get("event", "message")
                data = json.dumps(event)
                yield f"event: {event_type}\ndata: {data}\n\n"

        except Exception as e:
            error_data = json.dumps({"event": "error", "data": {"message": str(e)}})
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/estimate")
async def create_estimation(
    request: EstimationRequest,
    service: EstimationServiceDep,
) -> StreamingResponse:
    """Create a new catering estimation from a menu specification.

    Returns an SSE stream with real-time progress events.
    """
    menu_spec = request.model_dump()
    return await _sse_generator(service, menu_spec)


@router.get("/estimate/{estimation_id}")
async def get_estimation_status(
    estimation_id: str,
    service: EstimationServiceDep,
) -> EstimationStatusResponse:
    """Get the current status of an estimation job."""
    result = await service.get_estimation(estimation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Estimation not found")
    return EstimationStatusResponse(**result)


@router.post("/estimate/{estimation_id}/resume")
async def resume_estimation(
    estimation_id: str,
    service: EstimationServiceDep,
) -> StreamingResponse:
    """Resume an interrupted estimation.

    Only processes items that haven't been completed yet.
    Returns an SSE stream with remaining progress events.
    """
    # Verify the estimation exists
    existing = await service.get_estimation(estimation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Estimation not found")

    return await _sse_generator(service, {}, estimation_id=estimation_id)


async def _stats_sse_generator(
    service: EstimationService,
    menu_spec: dict[str, object],
    estimation_id: str | None = None,
    total_items: int | None = None,
) -> StreamingResponse:
    """Create an SSE StreamingResponse with stats-only events."""

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            if estimation_id:
                gen = service.resume_estimation(estimation_id)
                items = total_items or 0
            else:
                gen = service.create_estimation(menu_spec)
                categories = menu_spec.get("categories", {})
                items = sum(
                    len(cat) for cat in categories.values() if isinstance(cat, list)
                )

            async for event in stats_stream(gen, items, estimation_id):
                event_type = event.get("event", "stats")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

        except Exception as e:
            error_data = json.dumps({"event": "error", "data": {"message": str(e)}})
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/estimate/stream")
async def create_estimation_stream(
    request: EstimationRequest,
    service: EstimationServiceDep,
) -> StreamingResponse:
    """Create a new estimation and stream stats-only SSE events.

    Returns simplified progress events (elapsed, items_completed, tokens, etc.)
    instead of raw item/tool events. Use for clients that want stats without
    parsing the full event stream.
    """
    menu_spec = request.model_dump()
    return await _stats_sse_generator(service, menu_spec)


@router.post("/estimate/{estimation_id}/resume/stream")
async def resume_estimation_stream(
    estimation_id: str,
    service: EstimationServiceDep,
) -> StreamingResponse:
    """Resume an interrupted estimation and stream stats-only SSE events.

    Same stats format as POST /estimate/stream. Capture estimation_id from
    the first stats event before interrupting, then call this to resume.
    """
    existing = await service.get_estimation(estimation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Estimation not found")

    return await _stats_sse_generator(
        service, {}, estimation_id=estimation_id, total_items=existing["total_items"]
    )


@router.get("/health")
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse()
