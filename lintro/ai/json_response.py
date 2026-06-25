"""Shared JSON response parsing for AI products."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CliSchemaRequest",
    "load_json_object",
    "parse_fix_response_payload",
    "parse_review_response_payload",
    "parse_summary_response_payload",
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


def parse_review_response_payload(*, content: str) -> dict[str, Any]:
    """Parse and validate AI review JSON response.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed review response dictionary.

    Raises:
        ValueError: When JSON is invalid or missing required keys.
    """
    payload = load_json_object(content=content)

    for key in ("summary", "checklist", "findings"):
        if key not in payload:
            raise ValueError(f"Review response missing required key: {key}")

    return payload


def parse_summary_response_payload(*, content: str) -> dict[str, Any]:
    """Parse AI summary JSON response.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed summary payload dictionary.

    Raises:
        ValueError: When JSON is invalid or not an object.
    """
    return load_json_object(content=content)


def parse_fix_response_payload(*, content: str) -> dict[str, Any] | list[Any]:
    """Parse AI fix JSON response (single object or batch array).

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed fix payload as an object or array.

    Raises:
        ValueError: When JSON is invalid.
    """
    json_text = strip_json_fences(content=content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, (dict, list)):
        raise ValueError("Fix response must be a JSON object or array")

    return payload
