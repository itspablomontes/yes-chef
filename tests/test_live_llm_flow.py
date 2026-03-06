from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.schema_validator import validate_quote_schema
from app.infrastructure.catalog_index import build_catalog_index
from app.infrastructure.settings import get_settings
from app.main import create_app


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in text.split("\n\n"):
        if "data: " not in chunk:
            continue
        data_line = next(
            (line for line in chunk.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line is None:
            continue
        events.append(json.loads(data_line.replace("data: ", "", 1)))
    return events


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="Set RUN_LIVE_LLM_TESTS=1 and OPENAI_API_KEY to run live LLM flow tests.",
)
def test_live_llm_estimation_flow(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "live-test.sqlite"
    chroma_path = tmp_path / "live-chroma"

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("CHROMA_PATH", str(chroma_path))
    monkeypatch.setenv("ENABLE_VECTOR_SEARCH", "false")
    # Enable bounded parallelism in the live lane.
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")

    get_settings.cache_clear()
    build_catalog_index.cache_clear()

    menu_path = Path("artifacts/live-smoke/small_menu.json")
    payload = json.loads(menu_path.read_text(encoding="utf-8"))

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/estimate", json=payload)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    event_names = [event.get("event") for event in events]

    assert "quote_complete" in event_names
    assert "estimation_complete" in event_names
    assert "estimation_metrics" in event_names

    quote_event = next(event for event in events if event.get("event") == "quote_complete")
    quote_data = quote_event.get("data", {})
    assert isinstance(quote_data, dict)
    validate_quote_schema(quote_data)

    completion_event = next(event for event in events if event.get("event") == "estimation_complete")
    completion_data = completion_event.get("data", {})
    assert completion_data.get("status") in {"completed", "completed_with_failures"}
