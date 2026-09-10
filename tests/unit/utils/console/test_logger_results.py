"""Unit tests for ThreadSafeConsoleLogger tool result output methods.

Tests cover the print_tool_result method and its handling of various
actions and output content. Every assertion reads the text the method
actually printed (#2315).
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.console.logger import ThreadSafeConsoleLogger


def test_print_tool_result_outputs_content(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tool output is printed, followed by a blank separating line.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result("ruff", "Some output", 5)

    assert_that(capsys.readouterr().out).is_equal_to("Some output\n\n")


def test_print_tool_result_skips_empty_output(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty output prints nothing at all, not even a blank line.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result("ruff", "", 0)

    assert_that(capsys.readouterr().out).is_empty()
    assert_that(logger.get_buffer()).is_empty()


def test_print_tool_result_includes_metadata_for_check_action(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CHECK run surfaces the auto-fixable count parsed from raw output.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result(
        "ruff",
        "output",
        5,
        raw_output_for_meta="3 fixable issues",
        action=Action.CHECK,
    )

    assert_that(capsys.readouterr().out).contains(
        "Info: Found 3 auto-fixable issue(s)",
    )


def test_print_tool_result_skips_metadata_for_fix_action(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A FIX run has already resolved issues, so it prints no fixable count.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result(
        "ruff",
        "output",
        5,
        raw_output_for_meta="3 fixable issues",
        action=Action.FIX,
    )

    out = capsys.readouterr().out
    assert_that(out).is_equal_to("output\n\n")
    assert_that(out).does_not_contain("auto-fixable")


def test_print_tool_result_handles_pytest_for_test_action(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pytest TEST run appends the formatted test-results block.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result(
        "pytest",
        "test output",
        0,
        action=Action.TEST,
        success=True,
    )

    out = capsys.readouterr().out
    assert_that(out).contains("Test Results")
    assert_that(out).contains("All tests passed")


def test_print_tool_result_omits_pytest_block_for_other_tools(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-pytest tool under TEST gets no test-results block.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_result(
        "ruff",
        "test output",
        0,
        action=Action.TEST,
        success=True,
    )

    assert_that(capsys.readouterr().out).does_not_contain("Test Results")
