from __future__ import annotations

import asyncio

import httpx

import test_stream


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class FakeAsyncClient:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str) -> FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_apply_stream_event_handles_item_based_runtime_events() -> None:
    state = test_stream.StreamRunState(
        model_name="gpt-4o-mini",
        total_items=3,
        start_time=0.0,
    )

    test_stream.apply_stream_event(
        state,
        {"event": "item_started", "data": {"item_name": "Dish 1", "item_key": "appetizers:0"}},
    )
    test_stream.apply_stream_event(
        state,
        {"event": "tool_started", "data": {"tool": "search_catalog"}},
    )
    test_stream.apply_stream_event(
        state,
        {"event": "tool_waiting", "data": {"tool": "search_catalog", "elapsed_seconds": 4}},
    )
    test_stream.apply_stream_event(
        state,
        {"event": "validation_retry", "data": {"attempt": 2}},
    )
    test_stream.apply_stream_event(
        state,
        {"event": "item_complete", "data": {"item_name": "Dish 1", "item_key": "appetizers:0"}},
    )
    test_stream.apply_stream_event(
        state,
        {"event": "quote_complete", "data": {}},
    )

    assert state.last_item == "Dish 1"
    assert state.current_item_key == "appetizers:0"
    assert state.completed_items == 1
    assert state.active_tool == "Finalizing quote"


def test_probe_docker_health_reports_container_status(monkeypatch) -> None:
    monkeypatch.setattr(
        test_stream.httpx,
        "AsyncClient",
        lambda timeout=None: FakeAsyncClient(FakeResponse(200, {"status": "ok"})),
    )

    result = asyncio.run(test_stream.probe_docker_health("http://localhost:8000", timeout_seconds=2.0))

    assert result.ready is True
    assert result.url == "http://localhost:8000/health"
    assert result.message == "Docker API is healthy."


def test_probe_docker_health_handles_connection_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        test_stream.httpx,
        "AsyncClient",
        lambda timeout=None: FakeAsyncClient(httpx.ConnectError("refused")),
    )

    result = asyncio.run(test_stream.probe_docker_health("http://localhost:8000", timeout_seconds=2.0))

    assert result.ready is False
    assert "docker" in result.message.lower()


def test_build_run_summary_validates_real_quote() -> None:
    state = test_stream.StreamRunState(
        model_name="gpt-4o-mini",
        total_items=1,
        start_time=0.0,
        completed_items=1,
        last_item="Dish 1",
    )
    health = test_stream.HealthCheckResult(
        ready=True,
        url="http://localhost:8000/health",
        message="Docker API is healthy.",
        status_code=200,
    )
    final_quote = {
        "quote_id": "q1",
        "event": "Test Event",
        "generated_at": "2026-03-06T00:00:00Z",
        "line_items": [
            {
                "item_name": "Dish 1",
                "category": "appetizers",
                "ingredients": [
                    {
                        "name": "Salt",
                        "quantity": "1 tsp",
                        "unit_cost": 0.05,
                        "source": "estimated",
                        "sysco_item_number": None,
                    }
                ],
                "ingredient_cost_per_unit": 0.05,
            }
        ],
    }

    summary = test_stream.build_run_summary(
        health_check=health,
        state=state,
        completed_items=[{"item_name": "Dish 1", "ingredient_cost_per_unit": 0.05, "ingredients": []}],
        final_quote=final_quote,
        final_status="completed",
    )

    assert summary["api_reachable"] is True
    assert summary["schema_valid"] is True
    assert summary["completed_items"] == 1
    assert summary["final_status"] == "completed"
