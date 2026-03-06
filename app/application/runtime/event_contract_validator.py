"""Event contract validation — ensures SSE events have required fields."""

from __future__ import annotations

from typing import Any


class EventContractValidator:
    """Validates event payloads before emission. Raises ValueError on contract violation."""

    _REQUIRED: dict[str, list[str]] = {
        "item_complete": ["item_key"],
        "quote_complete": [],
        "estimation_complete": ["status"],
        "error": ["message"],
    }

    def validate(self, event: dict[str, Any]) -> None:
        """Validate event structure. Raises ValueError if required fields are missing."""
        event_type = event.get("event")
        if not event_type:
            raise ValueError("Event must have 'event' key")

        data = event.get("data")
        if data is None:
            data = {}

        required = self._REQUIRED.get(event_type, [])
        for field in required:
            value = data.get(field) if isinstance(data, dict) else None
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(
                    f"Event '{event_type}' requires non-empty '{field}' in data"
                )
