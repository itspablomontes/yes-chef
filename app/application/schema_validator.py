"""Helpers for validating quote output against the published JSON schema."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import ValidationError, Draft202012Validator


@lru_cache
def _quote_schema() -> dict[str, Any]:
    schema_path = Path(__file__).resolve().parents[2] / "data" / "quote_schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


@lru_cache
def _quote_validator() -> Draft202012Validator:
    return Draft202012Validator(_quote_schema())


def validate_quote_schema(quote: dict[str, Any]) -> None:
    """Raise ValueError if a quote does not satisfy the published schema."""
    try:
        _quote_validator().validate(quote)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at '{path}'" if path else ""
        raise ValueError(f"Quote schema validation failed{location}: {exc.message}") from exc
