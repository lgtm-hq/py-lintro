"""Tests for lintro.utils.streaming_output module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.streaming_output import (
    StreamingResultHandler,
    create_streaming_handler,
)


@pytest.fixture
def mock_tool_result() -> ToolResult:
    """Create a mock ToolResult for testing.

    Returns:
        A ToolResult with success=True and 2 issues.
    """
    return ToolResult(
        name="test_tool",
        success=True,
        issues_count=2,
        output="Test output",
    )


@pytest.fixture
def mock_tool_result_with_issues() -> ToolResult:
    """Create a mock ToolResult with issues.

    Returns:
        A ToolResult with success=False and 1 issue.
    """
    mock_issue = MagicMock()
    mock_issue.file = "test.py"
    mock_issue.line = 10
    mock_issue.column = 5
    mock_issue.message = "Test error"

    return ToolResult(
        name="test_tool",
        success=False,
        issues_count=1,
        output="Error output",
        issues=[mock_issue],
    )


@pytest.fixture
def mock_fix_result() -> ToolResult:
    """Create a mock ToolResult for fix action.

    Returns:
        A ToolResult with fix counts set.
    """
    return ToolResult(
        name="test_tool",
        success=True,
        issues_count=0,
        output="Fixed",
        fixed_issues_count=3,
        remaining_issues_count=1,
    )


def test_handler_stores_output_format() -> None:
    """Handler stores output format."""
    handler = StreamingResultHandler(output_format="json", action=Action.CHECK)
    assert_that(handler.output_format).is_equal_to("json")


def test_handler_stores_action() -> None:
    """Handler stores action."""
    handler = StreamingResultHandler(output_format="grid", action=Action.FIX)
    assert_that(handler.action).is_equal_to(Action.FIX)


def test_handler_initializes_totals() -> None:
    """Handler initializes totals dictionary."""
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    totals = handler.get_totals()

    assert_that(totals).contains_key("issues", "fixed", "remaining")
    assert_that(totals["issues"]).is_equal_to(0)


def test_handle_result_updates_totals(mock_tool_result: ToolResult) -> None:
    """Handle result updates totals.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(mock_tool_result)

    totals = handler.get_totals()
    assert_that(totals["tools_run"]).is_equal_to(1)
    assert_that(totals["issues"]).is_equal_to(2)


def test_handle_result_tracks_failures(
    mock_tool_result_with_issues: ToolResult,
) -> None:
    """Handle result tracks failed tools.

    Args:
        mock_tool_result_with_issues: Fixture providing a failed ToolResult.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(mock_tool_result_with_issues)

    totals = handler.get_totals()
    assert_that(totals["tools_failed"]).is_equal_to(1)


def test_handle_result_tracks_fix_counts(mock_fix_result: ToolResult) -> None:
    """Handle result tracks fix counts for FIX action.

    Args:
        mock_fix_result: Fixture providing a ToolResult with fix counts.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.FIX)
    handler.handle_result(mock_fix_result)

    totals = handler.get_totals()
    assert_that(totals["fixed"]).is_equal_to(3)
    assert_that(totals["remaining"]).is_equal_to(1)


def test_handle_result_buffers_results(mock_tool_result: ToolResult) -> None:
    """Handle result buffers results.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(mock_tool_result)

    results = handler.get_results()
    assert_that(results).is_length(1)
    assert_that(results[0].name).is_equal_to("test_tool")


def test_get_exit_code_returns_zero_on_success() -> None:
    """Get exit code returns 0 when all tools pass."""
    success_result = ToolResult(
        name="test_tool",
        success=True,
        issues_count=0,
    )
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(success_result)

    assert_that(handler.get_exit_code()).is_equal_to(0)


def test_get_exit_code_returns_one_on_failure(
    mock_tool_result_with_issues: ToolResult,
) -> None:
    """Get exit code returns 1 when tools fail.

    Args:
        mock_tool_result_with_issues: Fixture providing a failed ToolResult.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(mock_tool_result_with_issues)

    assert_that(handler.get_exit_code()).is_equal_to(1)


def test_get_exit_code_returns_one_on_issues(mock_tool_result: ToolResult) -> None:
    """Get exit code returns 1 when issues found in check mode.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    handler = StreamingResultHandler(output_format="grid", action=Action.CHECK)
    handler.handle_result(mock_tool_result)

    assert_that(handler.get_exit_code()).is_equal_to(1)


def test_context_manager_opens_file() -> None:
    """Context manager opens output file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        temp_path = f.name

    try:
        handler = StreamingResultHandler(
            output_format="jsonl",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            assert_that(handler._file_handle).is_not_none()
    finally:
        Path(temp_path).unlink()


def test_context_manager_closes_file() -> None:
    """Context manager closes output file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        temp_path = f.name

    try:
        handler = StreamingResultHandler(
            output_format="jsonl",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            file_handle = handler._file_handle
        assert_that(file_handle).is_not_none()
        assert file_handle is not None  # narrow type for mypy
        assert_that(file_handle.closed).is_true()
    finally:
        Path(temp_path).unlink()


def test_json_format_writes_array_brackets() -> None:
    """JSON format writes array brackets."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        temp_path = f.name

    try:
        handler = StreamingResultHandler(
            output_format="json",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            pass

        content = Path(temp_path).read_text()
        assert_that(content).starts_with("[")
        assert_that(content).ends_with("]")
    finally:
        Path(temp_path).unlink()


def test_handles_file_open_error() -> None:
    """Handler handles file open errors gracefully."""
    handler = StreamingResultHandler(
        output_format="jsonl",
        action=Action.CHECK,
        output_file="/nonexistent/directory/file.json",
    )
    with handler:
        assert_that(handler._file_handle).is_none()


def test_writes_jsonl_format(mock_tool_result: ToolResult) -> None:
    """Handler writes JSONL format.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
        temp_path = f.name

    try:
        handler = StreamingResultHandler(
            output_format="jsonl",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            handler.handle_result(mock_tool_result)

        content = Path(temp_path).read_text()
        lines = content.strip().split("\n")
        assert_that(lines).is_length(1)

        data = json.loads(lines[0])
        assert_that(data["tool"]).is_equal_to("test_tool")
    finally:
        Path(temp_path).unlink()


def test_writes_json_array_format(mock_tool_result: ToolResult) -> None:
    """Handler writes JSON array format.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        temp_path = f.name

    try:
        handler = StreamingResultHandler(
            output_format="json",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            handler.handle_result(mock_tool_result)

        content = Path(temp_path).read_text()
        data = json.loads(content)
        assert_that(data).is_instance_of(list)
        assert_that(data[0]["tool"]).is_equal_to("test_tool")
    finally:
        Path(temp_path).unlink()


def test_writes_multiple_json_array_results() -> None:
    """Handler writes multiple results in JSON array format."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        temp_path = f.name

    try:
        result1 = ToolResult(name="tool1", success=True, issues_count=0)
        result2 = ToolResult(name="tool2", success=True, issues_count=1)

        handler = StreamingResultHandler(
            output_format="json",
            action=Action.CHECK,
            output_file=temp_path,
        )
        with handler:
            handler.handle_result(result1)
            handler.handle_result(result2)

        content = Path(temp_path).read_text()
        data = json.loads(content)
        assert_that(data).is_instance_of(list)
        assert_that(data).is_length(2)
        assert_that(data[0]["tool"]).is_equal_to("tool1")
        assert_that(data[1]["tool"]).is_equal_to("tool2")
    finally:
        Path(temp_path).unlink()


def test_result_to_dict_includes_basic_fields(mock_tool_result: ToolResult) -> None:
    """Result dict includes basic fields.

    Args:
        mock_tool_result: Fixture providing a mock ToolResult.
    """
    handler = StreamingResultHandler(output_format="json", action=Action.CHECK)
    data = handler._result_to_dict(mock_tool_result)

    assert_that(data).contains_key("tool", "success", "issues_count")
    assert_that(data["tool"]).is_equal_to("test_tool")


def test_result_to_dict_includes_fix_counts(mock_fix_result: ToolResult) -> None:
    """Result dict includes fix counts when present.

    Args:
        mock_fix_result: Fixture providing a ToolResult with fix counts.
    """
    handler = StreamingResultHandler(output_format="json", action=Action.FIX)
    data = handler._result_to_dict(mock_fix_result)

    assert_that(data).contains_key("fixed_issues_count", "remaining_issues_count")
    assert_that(data["fixed_issues_count"]).is_equal_to(3)


def test_create_streaming_handler_with_format() -> None:
    """Create handler with specified format."""
    handler = create_streaming_handler("json", Action.CHECK)
    assert_that(handler.output_format).is_equal_to("json")


def test_create_streaming_handler_with_action() -> None:
    """Create handler with specified action."""
    handler = create_streaming_handler("grid", Action.FIX)
    assert_that(handler.action).is_equal_to(Action.FIX)


def test_create_streaming_handler_with_output_file(tmp_path: Path) -> None:
    """Create handler with output file.

    Args:
        tmp_path: Temporary path fixture.
    """
    output_file = tmp_path / "output.json"
    handler = create_streaming_handler("json", Action.CHECK, str(output_file))
    assert_that(handler.output_file).is_equal_to(str(output_file))
