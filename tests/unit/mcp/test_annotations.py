"""Unit tests for MCP capability annotation mapping."""

from __future__ import annotations

from assertpy import assert_that

from lintro.mcp.annotations import annotations_from_spec, tool_annotations_dict
from lintro.mcp.registry import McpToolSpec


def test_tool_annotations_dict_maps_hints() -> None:
    """Capability flags map to MCP readOnly/destructive/idempotent hints."""
    hints = tool_annotations_dict(
        read_only=True,
        destructive=False,
        idempotent=True,
    )

    assert_that(hints).is_equal_to(
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )


def test_annotations_from_spec_matches_flags() -> None:
    """McpToolSpec.to_annotations mirrors the registered capability flags."""

    def _handler(_arguments: dict[str, object]) -> dict[str, str]:
        return {"ok": "yes"}

    spec = McpToolSpec(
        name="demo",
        description="demo tool",
        input_schema={"type": "object", "properties": {}},
        handler=_handler,
        read_only=False,
        destructive=True,
        idempotent=False,
    )

    assert_that(annotations_from_spec(spec)).is_equal_to(spec.to_annotations())
    assert_that(spec.to_annotations()).is_equal_to(
        {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
