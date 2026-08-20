"""Tests for per-tool JSON / list-tools status values."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.tool_result_status import ToolResultStatus, status_for_tool_result
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import serialize_tool_result


def test_tool_result_status_values() -> None:
    """Enum values match the JSON contract."""
    assert_that(ToolResultStatus.OK).is_equal_to("ok")
    assert_that(ToolResultStatus.FAILED).is_equal_to("failed")
    assert_that(ToolResultStatus.SKIPPED).is_equal_to("skipped")
    assert_that(ToolResultStatus.UNAVAILABLE).is_equal_to("unavailable")


def test_status_for_tool_result_precedence() -> None:
    """Unavailable wins over skipped and success flags."""
    unavailable = ToolResult(
        name="ghost",
        success=True,
        output="",
        unavailable=True,
        skip_reason="not found",
    )
    skipped = ToolResult(
        name="ruff",
        success=True,
        output="",
        skipped=True,
        skip_reason="no files",
    )
    failed = ToolResult(
        name="ruff",
        success=False,
        output="",
    )
    ok = ToolResult(
        name="ruff",
        success=True,
        output="",
    )
    assert_that(status_for_tool_result(unavailable)).is_equal_to(
        ToolResultStatus.UNAVAILABLE,
    )
    assert_that(status_for_tool_result(skipped)).is_equal_to(ToolResultStatus.SKIPPED)
    assert_that(status_for_tool_result(failed)).is_equal_to(ToolResultStatus.FAILED)
    assert_that(status_for_tool_result(ok)).is_equal_to(ToolResultStatus.OK)


def test_serialize_tool_result_status_uses_shared_enum() -> None:
    """JSON status is the ToolResultStatus value, not a local literal."""
    result = ToolResult(
        name="ghost",
        success=True,
        output="ghost unavailable",
        unavailable=True,
        skip_reason="not found",
    )
    data = serialize_tool_result(result, action=Action.CHECK)
    assert_that(data["status"]).is_equal_to(ToolResultStatus.UNAVAILABLE)
    assert_that(data["unavailable"]).is_true()
