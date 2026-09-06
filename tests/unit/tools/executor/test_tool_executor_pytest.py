"""Tests for pytest-specific tool executor functionality."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.tool_configuration import get_tools_to_run
from lintro.utils.tool_executor import run_lint_tools_simple


def test_get_tools_to_run_test_action_with_pytest() -> None:
    """Test get_tools_to_run with test action returns pytest."""
    result = get_tools_to_run(tools="pytest", action="test")
    assert_that(result.to_run).is_length(1)
    assert_that(result.to_run[0]).is_equal_to("pytest")


def test_get_tools_to_run_test_action_with_none_tools() -> None:
    """Test get_tools_to_run with test action and None tools."""
    result = get_tools_to_run(tools=None, action="test")
    assert_that(result.to_run).is_length(1)
    assert_that(result.to_run[0]).is_equal_to("pytest")


def test_get_tools_to_run_test_action_with_invalid_tool() -> None:
    """Test get_tools_to_run raises error with invalid tool for test action."""
    with pytest.raises(ValueError, match="(?i)only.*pytest.*supported"):
        get_tools_to_run(tools="ruff", action="test")


def test_get_tools_to_run_test_action_with_multiple_tools() -> None:
    """Test get_tools_to_run raises error with multiple tools for test action."""
    with pytest.raises(ValueError, match="(?i)only.*pytest.*supported"):
        get_tools_to_run(tools="pytest,ruff", action="test")


def test_get_tools_to_run_check_action_rejects_pytest() -> None:
    """Test get_tools_to_run rejects pytest for check action."""
    with pytest.raises(ValueError, match="not available for check"):
        get_tools_to_run(tools="pytest", action="check")


def test_get_tools_to_run_format_action_rejects_pytest() -> None:
    """Test get_tools_to_run rejects pytest for format action."""
    with pytest.raises(ValueError, match="not available for check/fmt"):
        get_tools_to_run(tools="pytest", action="fmt")


def test_get_tools_to_run_test_action_unavailable() -> None:
    """Test get_tools_to_run with test action ensures pytest is available."""
    # Verify that pytest is available in the registry
    result = get_tools_to_run(tools=None, action="test")
    assert_that(result.to_run).is_not_empty()
    assert_that(result.to_run[0]).is_equal_to("pytest")


def test_get_tools_to_run_check_action_filters_out_pytest() -> None:
    """Test get_tools_to_run filters pytest out for check action."""
    result = get_tools_to_run(tools="all", action="check")
    # Should not contain pytest
    assert_that(result.to_run).does_not_contain("pytest")


def test_get_tools_to_run_format_action_filters_out_pytest() -> None:
    """Test get_tools_to_run filters pytest out for format action."""
    result = get_tools_to_run(tools="all", action="fmt")
    # Should not contain pytest
    assert_that(result.to_run).does_not_contain("pytest")


def test_run_lint_tools_simple_test_action_basic() -> None:
    """Test run_lint_tools_simple with test action."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.tool_manager") as mock_manager,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger") as mock_logger,
    ):
        mock_logger_inst = Mock()
        mock_logger.return_value = mock_logger_inst
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst

        mock_pytest_tool = Mock()
        mock_pytest_tool.name = "pytest"
        mock_pytest_tool.copy_for_execution.return_value = mock_pytest_tool
        mock_pytest_tool.check.return_value = ToolResult(
            name="pytest",
            success=True,
            issues=[],
            issues_count=0,
            output="All tests passed",
        )
        mock_manager.get_tool.return_value = mock_pytest_tool

        result = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="pytest",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=False,
            raw_output=False,
        )

        assert_that(result).is_equal_to(0)


def test_run_lint_tools_simple_test_action_with_failures() -> None:
    """Test run_lint_tools_simple with test failures."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.tool_manager") as mock_manager,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger") as mock_logger,
    ):
        mock_logger_inst = Mock()
        mock_logger.return_value = mock_logger_inst
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst

        mock_pytest_tool = Mock()
        mock_pytest_tool.name = "pytest"
        mock_pytest_tool.copy_for_execution.return_value = mock_pytest_tool
        mock_pytest_tool.check.return_value = ToolResult(
            name="pytest",
            success=False,
            issues_count=2,
            issues=[],
            output="2 test failures",
        )
        mock_manager.get_tool.return_value = mock_pytest_tool

        result = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="pytest",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=False,
            raw_output=False,
        )

        assert_that(result).is_equal_to(1)


def test_run_lint_tools_simple_test_action_invalid_tool() -> None:
    """Test run_lint_tools_simple with invalid tool for test action."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger") as mock_logger,
    ):
        mock_logger_inst = Mock()
        mock_logger.return_value = mock_logger_inst
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst

        result = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=False,
            raw_output=False,
        )

        # Should return failure when tool is not available
        assert_that(result).is_equal_to(1)


def _fake_pytest_tool(*, applied: list[dict[str, object]]) -> Mock:
    """Build a fake pytest plugin that records the options applied to it.

    Args:
        applied: List that accumulates one entry per ``set_options`` call.

    Returns:
        A mock plugin reporting a clean pytest run.
    """
    tool = Mock()
    tool.name = "pytest"
    tool.copy_for_execution.return_value = tool
    tool.set_options.side_effect = lambda **kwargs: applied.append(kwargs)
    tool.check.return_value = ToolResult(
        name="pytest",
        success=True,
        issues=[],
        issues_count=0,
        output="All tests passed",
    )
    return tool


def test_run_lint_tools_simple_test_action_with_tool_options() -> None:
    """Parsed pytest tool options are applied and the run reports success."""
    applied: list[dict[str, object]] = []

    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.tool_manager") as mock_manager,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger"),
    ):
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst
        mock_manager.get_tool.return_value = _fake_pytest_tool(applied=applied)

        exit_code = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="pytest",
            tool_options="pytest:maxfail=5,pytest:tb=long",
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=False,
            raw_output=False,
        )

    merged = {key: value for options in applied for key, value in options.items()}
    assert_that(merged).contains_key("maxfail", "tb")
    assert_that(str(merged["maxfail"])).is_equal_to("5")
    assert_that(str(merged["tb"])).is_equal_to("long")
    assert_that(exit_code).is_equal_to(0)


def test_run_lint_tools_simple_test_action_exclude_patterns() -> None:
    """The exclude pattern reaches the tool and the run reports success."""
    applied: list[dict[str, object]] = []

    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.tool_manager") as mock_manager,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger"),
    ):
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst
        mock_manager.get_tool.return_value = _fake_pytest_tool(applied=applied)

        exit_code = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="pytest",
            tool_options=None,
            exclude="*.venv",
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=False,
            raw_output=False,
        )

    merged = {key: value for options in applied for key, value in options.items()}
    assert_that(merged).contains_key("exclude_patterns")
    assert_that(merged["exclude_patterns"]).contains("*.venv")
    assert_that(exit_code).is_equal_to(0)


def test_run_lint_tools_simple_test_action_verbose() -> None:
    """A verbose test run still reports the tool's clean result as success."""
    applied: list[dict[str, object]] = []

    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("lintro.utils.tool_executor.tool_manager") as mock_manager,
        patch("lintro.utils.tool_executor.OutputManager") as mock_output,
        patch("lintro.utils.console.create_logger"),
    ):
        mock_output_inst = Mock()
        mock_output_inst.run_dir = Path(tmpdir)
        mock_output.return_value = mock_output_inst
        mock_manager.get_tool.return_value = _fake_pytest_tool(applied=applied)

        exit_code = run_lint_tools_simple(
            action="test",
            paths=["."],
            tools="pytest",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="plain",
            verbose=True,
            raw_output=False,
        )

    assert_that(exit_code).is_equal_to(0)
