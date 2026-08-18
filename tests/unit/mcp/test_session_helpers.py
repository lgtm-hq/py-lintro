"""Unit tests for the in-memory MCP 2.x session helpers."""

from __future__ import annotations

import pytest
from mcp.types import CallToolResult, TextContent

from tests.unit.mcp.session_helpers import payload_from_result


def test_payload_from_result_requires_matching_structured_and_text() -> None:
    """Both representations must carry the same object."""
    result = CallToolResult(
        is_error=False,
        structured_content={"status": "ok"},
        content=[
            TextContent(type="text", text='{"status": "ok"}'),
        ],
    )

    assert payload_from_result(result) == {"status": "ok"}


def test_payload_from_result_keeps_empty_structured_content() -> None:
    """An explicit empty object is a payload when the text block matches."""
    result = CallToolResult(
        is_error=False,
        structured_content={},
        content=[
            TextContent(type="text", text="{}"),
        ],
    )

    assert payload_from_result(result) == {}


def test_payload_from_result_rejects_missing_structured_content() -> None:
    """A text-only result is not the production contract."""
    result = CallToolResult(
        is_error=False,
        content=[
            TextContent(type="text", text='{"echo": "hi"}'),
        ],
    )

    with pytest.raises(AssertionError):
        payload_from_result(result)


def test_payload_from_result_rejects_conflicting_representations() -> None:
    """Drift between structured content and the text block must fail the test."""
    result = CallToolResult(
        is_error=False,
        structured_content={"status": "ok"},
        content=[
            TextContent(type="text", text='{"status": "ignored"}'),
        ],
    )

    with pytest.raises(AssertionError):
        payload_from_result(result)
