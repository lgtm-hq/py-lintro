"""Unit tests for the in-memory MCP 2.x session helpers."""

from __future__ import annotations

from mcp.types import CallToolResult, TextContent

from tests.unit.mcp.session_helpers import payload_from_result


def test_payload_from_result_prefers_structured_content() -> None:
    """Structured content wins when the SDK populates both representations."""
    result = CallToolResult(
        is_error=False,
        structured_content={"status": "ok"},
        content=[
            TextContent(type="text", text='{"status": "ignored"}'),
        ],
    )

    assert payload_from_result(result) == {"status": "ok"}


def test_payload_from_result_falls_back_to_json_text() -> None:
    """A text-only result still decodes as the tool payload."""
    result = CallToolResult(
        is_error=False,
        content=[
            TextContent(type="text", text='{"echo": "hi"}'),
        ],
    )

    assert payload_from_result(result) == {"echo": "hi"}
