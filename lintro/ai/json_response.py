"""Shared JSON response parsing for AI products."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
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


def _is_parseable_json(text: str) -> bool:
    """Return True when ``text`` parses as JSON.

    Args:
        text: Candidate JSON string.

    Returns:
        True if ``json.loads`` succeeds, else False.
    """
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _parses_as_object(text: str) -> bool:
    """Return True when ``text`` parses as a JSON object (``dict``).

    Args:
        text: Candidate JSON string.

    Returns:
        True if ``json.loads`` yields a ``dict``, else False.
    """
    try:
        return isinstance(json.loads(text), dict)
    except (json.JSONDecodeError, ValueError):
        return False


def _iter_balanced_json_spans(text: str) -> Iterator[str]:
    """Yield each top-level balanced JSON object/array span found in text.

    Scans left to right and, for every ``{`` or ``[`` encountered at the top
    level, emits the substring through its matching close bracket while
    respecting string literals and escapes. After a span closes, scanning
    resumes just past it, so a response with a decoy span (for example
    ``Checklist [1]``) followed by the real payload yields both spans in order.

    Args:
        text: Raw text that may embed one or more JSON spans in prose.

    Yields:
        str: Each balanced JSON substring, in the order it appears.
    """
    open_to_close = {"{": "}", "[": "]"}
    length = len(text)
    index = 0
    while index < length:
        opener = text[index]
        if opener not in open_to_close:
            index += 1
            continue
        closer = open_to_close[opener]
        depth = 0
        in_string = False
        escape_next = False
        end = -1
        for scan in range(index, length):
            char = text[scan]
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                if in_string:
                    escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    end = scan
                    break
        if end == -1:
            # Unbalanced from here on; no further complete spans possible.
            return
        yield text[index : end + 1]
        index = end + 1


def _extract_json_span(text: str, *, expect_object: bool = False) -> str | None:
    """Extract a balanced JSON object or array span embedded in prose.

    Iterates every top-level balanced span in ``text``. When ``expect_object``
    is set, the last span that parses as a JSON object wins, so an earlier
    decoy array or scalar span (for example ``[1]`` before the real
    ``{...}``) can never shadow the intended object payload. When no object is
    found (or ``expect_object`` is unset), the last span that parses as any
    JSON value is returned, falling back to the first balanced span.

    Args:
        text: Raw text that may embed a JSON object or array in prose.
        expect_object: When True, bias extraction toward a JSON object span.

    Returns:
        The extracted JSON substring, or ``None`` when no balanced span is
        found.
    """
    spans = list(_iter_balanced_json_spans(text))
    if not spans:
        return None

    if expect_object:
        for span in reversed(spans):
            if _parses_as_object(span):
                return span

    for span in reversed(spans):
        if _is_parseable_json(span):
            return span
    return spans[0]


def strip_json_fences(*, content: str, expect_object: bool = False) -> str:
    """Strip markdown JSON code fences from model output.

    Robust against responses that prepend prose containing a decoy fenced
    block before the real JSON payload: all fenced blocks are considered and
    the last one that parses as valid JSON wins (models place their final
    answer last). When no fenced block parses, the last fenced block is
    returned. When there are no fences at all, falls back to brace/bracket
    matched extraction before returning the raw stripped content.

    When ``expect_object`` is set, extraction is biased toward a JSON object
    so an earlier decoy array or scalar span (for example ``[1]`` in prose
    before the real ``{...}``) cannot shadow the intended object payload.

    Args:
        content: Raw model response text.
        expect_object: When True, prefer a JSON object over an earlier array
            or scalar when isolating the payload.

    Returns:
        JSON string suitable for ``json.loads``.
    """
    stripped = content.strip()
    blocks: list[str] = _JSON_FENCE_PATTERN.findall(stripped)
    if blocks:
        candidates = [block.strip() for block in blocks]
        if expect_object:
            for candidate in reversed(candidates):
                if _parses_as_object(candidate):
                    return candidate
        for candidate in reversed(candidates):
            if _is_parseable_json(candidate):
                return candidate
        return candidates[-1]

    if expect_object:
        if _parses_as_object(stripped):
            return stripped
        extracted = _extract_json_span(stripped, expect_object=True)
        if extracted is not None and _is_parseable_json(extracted):
            return extracted
        return stripped

    if not _is_parseable_json(stripped):
        extracted = _extract_json_span(stripped)
        if extracted is not None and _is_parseable_json(extracted):
            return extracted
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
    json_text = strip_json_fences(content=content, expect_object=True)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON response must be an object")

    return payload


def load_json_value(*, content: str) -> dict[str, Any] | list[Any]:
    """Parse fenced or raw JSON into an object or array.

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed JSON object or array.

    Raises:
        ValueError: When JSON is invalid or not an object/array.
    """
    json_text = strip_json_fences(content=content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, (dict, list)):
        raise ValueError("JSON response must be an object or array")

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
    """
    return load_json_object(content=content)


def parse_fix_response_payload(*, content: str) -> dict[str, Any] | list[Any]:
    """Parse AI fix JSON response (single object or batch array).

    Args:
        content: Raw or fenced JSON model response.

    Returns:
        Parsed fix payload as an object or array.
    """
    return load_json_value(content=content)
