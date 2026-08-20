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
    assert_that(ToolResultStatus.UNKNOWN).is_equal_to("unknown")


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


def test_status_for_tool_result_timed_out_maps_to_failed() -> None:
    """Timeouts classify as failed while ``timed_out`` carries detail."""
    timed_out = ToolResult(
        name="mypy",
        success=False,
        output="timed out",
        timed_out=True,
    )

    assert_that(status_for_tool_result(timed_out)).is_equal_to(
        ToolResultStatus.FAILED,
    )


def test_status_for_tool_result_issues_map_to_failed() -> None:
    """Issue counts classify as failed even when success is ambiguous."""
    with_issues = ToolResult(
        name="ruff",
        success=False,
        output="",
        issues_count=2,
    )

    assert_that(
        status_for_tool_result(with_issues, issue_count=2),
    ).is_equal_to(ToolResultStatus.FAILED)


def test_status_for_tool_result_missing_success_defaults_to_failed() -> None:
    """Missing ``success`` fails closed like ToolResult's default."""
    bare = ToolResult(name="ruff", output="")

    assert_that(status_for_tool_result(bare)).is_equal_to(ToolResultStatus.FAILED)


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


def test_serialize_tool_result_timed_out_status_is_failed() -> None:
    """Timed-out tools serialize ``status: failed`` with ``timed_out: true``."""
    result = ToolResult(
        name="mypy",
        success=False,
        output="timed out",
        timed_out=True,
    )
    data = serialize_tool_result(result, action=Action.CHECK)

    assert_that(data["status"]).is_equal_to(ToolResultStatus.FAILED)
    assert_that(data["timed_out"]).is_true()
    assert_that(data["success"]).is_false()
