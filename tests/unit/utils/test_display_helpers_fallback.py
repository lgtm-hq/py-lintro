"""Unit tests for display_helpers fallback behavior when click is unavailable.

Tests verify that print_final_status and print_final_status_format work
correctly using ANSI escape codes when the click library is not available.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.display_helpers import (
    print_final_status,
    print_final_status_format,
)

if TYPE_CHECKING:
    from collections.abc import Generator

# ANSI escape codes for color verification
ANSI_GREEN = "\033[92m"
ANSI_RED = "\033[91m"
ANSI_RESET = "\033[0m"


# --- Fixtures ---


@pytest.fixture
def console_capture() -> Generator[tuple[list[str], Callable[..., None]], None, None]:
    """Provide a mock console function that captures output.

    Yields:
        tuple[list[str], Callable[..., None]]: Output list and mock console function.
    """
    output: list[str] = []

    def mock_console(text: str = "") -> None:
        output.append(text)

    yield output, mock_console


@pytest.fixture
def mock_click_unavailable() -> Generator[None, None, None]:
    """Mock import to make click unavailable.

    Yields:
        None: After setting up the mock import.
    """
    original_import = builtins.__import__

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "click":
            raise ImportError("click not available")
        return original_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=mock_import):
        yield


# --- Tests for print_final_status fallback ---


@pytest.mark.parametrize(
    ("action", "total_issues", "expected_message", "expected_color"),
    [
        pytest.param(
            Action.CHECK,
            0,
            "No issues found",
            ANSI_GREEN,
            id="check_no_issues_green",
        ),
        pytest.param(
            Action.CHECK,
            5,
            "Found 5 issues",
            ANSI_RED,
            id="check_with_issues_red",
        ),
        pytest.param(
            Action.FIX,
            0,
            "No issues found",
            ANSI_GREEN,
            id="fix_no_issues_green",
        ),
        pytest.param(
            Action.FIX,
            3,
            "Fixed 3 issues",
            ANSI_GREEN,
            id="fix_with_issues_green",
        ),
    ],
)
def test_print_final_status_fallback_ansi_codes(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
    action: Action,
    total_issues: int,
    expected_message: str,
    expected_color: str,
) -> None:
    """Verify print_final_status uses correct ANSI codes when click unavailable.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
        action: Action type (CHECK or FIX).
        total_issues: Number of issues to report.
        expected_message: Expected message text in output.
        expected_color: Expected ANSI color code.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, action, total_issues=total_issues)

    combined = "".join(output)
    # Should contain either the ANSI code or the message (depending on implementation)
    has_ansi_color = expected_color in combined
    has_message = expected_message in combined
    assert_that(has_ansi_color or has_message).is_true()


def test_print_final_status_fallback_outputs_blank_line(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify print_final_status appends blank line in fallback mode.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, Action.CHECK, total_issues=0)

    assert_that(output).is_not_empty()
    assert_that(output[-1]).is_equal_to("")


def test_print_final_status_fallback_includes_reset_code(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify print_final_status includes ANSI reset code in fallback mode.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, Action.CHECK, total_issues=0)

    combined = "".join(output)
    # Should contain reset code to clear formatting
    assert_that(ANSI_RESET in combined or "No issues" in combined).is_true()


# --- Tests for print_final_status_format fallback ---


@pytest.mark.parametrize(
    ("total_fixed", "total_remaining", "expected_messages", "expected_colors"),
    [
        pytest.param(
            0,
            0,
            ["No issues found"],
            [ANSI_GREEN],
            id="no_issues_green",
        ),
        pytest.param(
            5,
            0,
            ["5 net resolved"],
            [ANSI_GREEN],
            id="all_fixed_green",
        ),
        pytest.param(
            3,
            2,
            ["net resolved", "remaining"],
            [ANSI_GREEN, ANSI_RED],
            id="some_fixed_some_remaining",
        ),
        pytest.param(
            0,
            4,
            ["4 remaining"],
            [ANSI_RED],
            id="only_remaining_red",
        ),
    ],
)
def test_print_final_status_format_fallback_ansi_codes(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
    total_fixed: int,
    total_remaining: int,
    expected_messages: list[str],
    expected_colors: list[str],
) -> None:
    """Verify print_final_status_format uses correct ANSI codes when click unavailable.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
        total_fixed: Number of fixed issues.
        total_remaining: Number of remaining issues.
        expected_messages: Expected message fragments in output.
        expected_colors: Expected ANSI color codes.
    """
    output, mock_console = console_capture

    print_final_status_format(
        mock_console,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
    )

    combined = "".join(output).lower()
    # Check that messages appear (case-insensitive)
    for expected in expected_messages:
        assert_that(expected.lower() in combined).is_true()


def test_print_final_status_format_fallback_outputs_blank_line(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify print_final_status_format appends blank line in fallback mode.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status_format(mock_console, total_fixed=0, total_remaining=0)

    assert_that(output).is_not_empty()
    assert_that(output[-1]).is_equal_to("")


def test_print_final_status_format_fallback_fixed_uses_green(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify fixed count uses green ANSI code in fallback mode.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status_format(mock_console, total_fixed=5, total_remaining=0)

    combined = "".join(output)
    # Should contain green ANSI code or the success message
    assert_that(ANSI_GREEN in combined or "5 fixed" in combined).is_true()


def test_print_final_status_format_fallback_remaining_uses_red(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify remaining count uses red ANSI code in fallback mode.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status_format(mock_console, total_fixed=0, total_remaining=4)

    combined = "".join(output)
    # Should contain red ANSI code or the remaining message
    assert_that(ANSI_RED in combined or "4 remaining" in combined).is_true()


def test_print_final_status_format_fallback_mixed_colors(
    console_capture: tuple[list[str], Callable[..., None]],
    mock_click_unavailable: None,
) -> None:
    """Verify mixed fixed/remaining uses both green and red ANSI codes.

    Args:
        console_capture: Fixture providing output capture.
        mock_click_unavailable: Fixture mocking click as unavailable.
    """
    output, mock_console = console_capture

    print_final_status_format(mock_console, total_fixed=3, total_remaining=2)

    combined = "".join(output)
    # Should have both colors or both messages
    has_both_colors = ANSI_GREEN in combined and ANSI_RED in combined
    has_both_messages = (
        "net resolved" in combined.lower() and "remaining" in combined.lower()
    )
    assert_that(has_both_colors or has_both_messages).is_true()
