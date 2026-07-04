"""Shared JSON response parsing for AI products."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CliSchemaRequest",
    "load_json_object",
    "strip_json_fences",
]

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class CliSchemaRequest:
    """Native CLI schema arguments for structured output (Phase 14).

    Attributes:
        schema: JSON Schema object passed to provider-native flags.
        schema_name: Optional provider-specific schema identifier.
    """

    schema: dict[str, Any]
    schema_name: str | None = None


def strip_json_fences(*, content: str) -> str:
    """Strip markdown JSON code fences from model output.

    Args:
        content: Raw model response text.

    Returns:
        JSON string suitable for ``json.loads``.
    """
    stripped = content.strip()
    match = _JSON_FENCE_PATTERN.search(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def load_json_object(*, content: str) -> dict[str, Any]:
    """Parse fenced or raw JSON into an object with clear errors.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: When JSON is invalid or not an object.
    """
    json_text = strip_json_fences(content=content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON response must be an object")

    return payload
