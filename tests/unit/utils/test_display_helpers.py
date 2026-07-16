"""Unit tests for display_helpers module.

Tests for ASCII art display, final status printing, and module constants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.display_helpers import (
    BORDER_LENGTH,
    INFO_BORDER_LENGTH,
    print_ascii_art,
    print_final_status,
    print_final_status_format,
)

if TYPE_CHECKING:
    from collections.abc import Generator


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


# --- Tests for print_ascii_art ---


@pytest.mark.parametrize(
    ("issue_count", "expected_file", "art_content", "expected_in_output"),
    [
        pytest.param(
            0,
            "success.txt",
            ["  \\o/  ", " SUCCESS "],
            "SUCCESS",
            id="zero_issues_loads_success_art",
        ),
        pytest.param(
            5,
            "fail.txt",
            [" FAIL ", " x_x "],
            "FAIL",
            id="nonzero_issues_loads_fail_art",
        ),
        pytest.param(
            1,
            "fail.txt",
            [" FAIL ", " x_x "],
            "FAIL",
            id="single_issue_loads_fail_art",
        ),
    ],
)
@patch("lintro.utils.display_helpers.is_interactive_tty", return_value=True)
@patch("lintro.utils.display_helpers.read_ascii_art")
def test_print_ascii_art_selects_correct_file(
    mock_read: MagicMock,
    _mock_tty: MagicMock,
    console_capture: tuple[list[str], Callable[..., None]],
    issue_count: int,
    expected_file: str,
    art_content: list[str],
    expected_in_output: str,
) -> None:
    """Verify print_ascii_art loads the correct file based on issue count.

    Args:
        mock_read: Mock for read_ascii_art function.
        _mock_tty: Mock forcing interactive TTY precondition.
        console_capture: Fixture providing output capture.
        issue_count: Number of issues to simulate.
        expected_file: Expected filename to be loaded.
        art_content: Mock art content to return.
        expected_in_output: Text expected in console output.
    """
    output, mock_console = console_capture
    mock_read.return_value = art_content

    print_ascii_art(mock_console, issue_count=issue_count)

    mock_read.assert_called_once_with(filename=expected_file)
    assert_that(output).is_length(1)
    assert_that(output[0]).contains(expected_in_output)


@patch("lintro.utils.display_helpers.is_interactive_tty", return_value=True)
@patch("lintro.utils.display_helpers.read_ascii_art")
def test_print_ascii_art_no_output_when_empty(
    mock_read: MagicMock,
    _mock_tty: MagicMock,
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify no output is produced when ASCII art is empty.

    Args:
        mock_read: Mock for read_ascii_art function.
        _mock_tty: Mock forcing interactive TTY precondition.
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture
    mock_read.return_value = []

    print_ascii_art(mock_console, issue_count=0)

    assert_that(output).is_empty()


@patch("lintro.utils.display_helpers.is_interactive_tty", return_value=True)
@patch("lintro.utils.display_helpers.read_ascii_art")
def test_print_ascii_art_handles_exception_gracefully(
    mock_read: MagicMock,
    _mock_tty: MagicMock,
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify exceptions are handled gracefully without crashing.

    Args:
        mock_read: Mock for read_ascii_art function.
        _mock_tty: Mock forcing interactive TTY precondition.
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture
    mock_read.side_effect = FileNotFoundError("Art file not found")

    # Should not raise, just log debug
    print_ascii_art(mock_console, issue_count=0)

    assert_that(output).is_empty()


@patch("lintro.utils.display_helpers.is_interactive_tty", return_value=False)
@patch("lintro.utils.display_helpers.read_ascii_art")
def test_print_ascii_art_skips_when_not_tty(
    mock_read: MagicMock,
    _mock_tty: MagicMock,
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify ASCII art is skipped when not on an interactive TTY.

    Args:
        mock_read: Mock for read_ascii_art function.
        _mock_tty: Mock forcing non-interactive TTY precondition.
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture

    print_ascii_art(mock_console, issue_count=0)

    mock_read.assert_not_called()
    assert_that(output).is_empty()


# --- Tests for print_final_status ---


@pytest.mark.parametrize(
    ("action", "total_issues", "expected_message"),
    [
        pytest.param(
            Action.CHECK,
            0,
            "No issues found",
            id="check_no_issues_shows_success",
        ),
        pytest.param(
            Action.CHECK,
            5,
            "Found 5 issues",
            id="check_with_issues_shows_count",
        ),
        pytest.param(
            Action.CHECK,
            1,
            "Found 1 issues",
            id="check_single_issue_shows_count",
        ),
        pytest.param(
            Action.FIX,
            0,
            "No issues found",
            id="fix_no_issues_shows_success",
        ),
        pytest.param(
            Action.FIX,
            3,
            "Fixed 3 issues",
            id="fix_with_issues_shows_fixed_count",
        ),
        pytest.param(
            Action.FIX,
            1,
            "Fixed 1 issues",
            id="fix_single_issue_shows_fixed_count",
        ),
    ],
)
def test_print_final_status_message_content(
    console_capture: tuple[list[str], Callable[..., None]],
    action: Action,
    total_issues: int,
    expected_message: str,
) -> None:
    """Verify print_final_status displays correct message for action and issue count.

    Args:
        console_capture: Fixture providing output capture.
        action: Action type (CHECK or FIX).
        total_issues: Number of issues to report.
        expected_message: Expected text in output.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, action, total_issues=total_issues)

    combined = "".join(output)
    assert_that(combined).contains(expected_message)


def test_print_final_status_outputs_blank_line_at_end(
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify print_final_status appends a blank line after the status message.

    Args:
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, Action.CHECK, total_issues=0)

    assert_that(output).is_not_empty()
    assert_that(output[-1]).is_equal_to("")


def test_print_final_status_produces_output(
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify print_final_status produces at least some output.

    Args:
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture

    print_final_status(mock_console, Action.CHECK, total_issues=0)

    assert_that(len(output)).is_greater_than(0)


# --- Tests for print_final_status_format ---


@pytest.mark.parametrize(
    ("total_fixed", "total_remaining", "expected_messages"),
    [
        pytest.param(
            0,
            0,
            ["No issues found"],
            id="no_issues_no_fixes",
        ),
        pytest.param(
            5,
            0,
            ["5 fixed"],
            id="all_fixed_no_remaining",
        ),
        pytest.param(
            3,
            2,
            ["3 fixed", "2 remaining"],
            id="some_fixed_some_remaining",
        ),
        pytest.param(
            0,
            4,
            ["4 remaining"],
            id="none_fixed_some_remaining",
        ),
        pytest.param(
            10,
            5,
            ["10 fixed", "5 remaining"],
            id="many_fixed_some_remaining",
        ),
    ],
)
def test_print_final_status_format_message_content(
    console_capture: tuple[list[str], Callable[..., None]],
    total_fixed: int,
    total_remaining: int,
    expected_messages: list[str],
) -> None:
    """Verify print_final_status_format displays correct messages for fixed/remaining.

    Args:
        console_capture: Fixture providing output capture.
        total_fixed: Number of fixed issues.
        total_remaining: Number of remaining issues.
        expected_messages: List of expected text fragments in output.
    """
    output, mock_console = console_capture

    print_final_status_format(
        mock_console,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
    )

    combined = "".join(output)
    for expected in expected_messages:
        assert_that(combined).contains(expected)


def test_print_final_status_format_outputs_blank_line_at_end(
    console_capture: tuple[list[str], Callable[..., None]],
) -> None:
    """Verify print_final_status_format appends a blank line after messages.

    Args:
        console_capture: Fixture providing output capture.
    """
    output, mock_console = console_capture

    print_final_status_format(mock_console, total_fixed=0, total_remaining=0)

    assert_that(output).is_not_empty()
    assert_that(output[-1]).is_equal_to("")


# --- Tests for module constants ---


def test_border_length_is_positive_integer() -> None:
    """Verify BORDER_LENGTH is a positive integer suitable for border formatting."""
    assert_that(BORDER_LENGTH).is_instance_of(int)
    assert_that(BORDER_LENGTH).is_greater_than(0)
    assert_that(BORDER_LENGTH).is_equal_to(50)


def test_info_border_length_is_positive_integer() -> None:
    """Verify INFO_BORDER_LENGTH is a positive integer suitable for info borders."""
    assert_that(INFO_BORDER_LENGTH).is_instance_of(int)
    assert_that(INFO_BORDER_LENGTH).is_greater_than(0)
    assert_that(INFO_BORDER_LENGTH).is_equal_to(40)


def test_border_length_can_create_border_string() -> None:
    """Verify BORDER_LENGTH produces a usable border string."""
    border = "=" * BORDER_LENGTH
    assert_that(border).is_length(BORDER_LENGTH)
    assert_that(border).is_equal_to("=" * 50)


def test_info_border_length_can_create_border_string() -> None:
    """Verify INFO_BORDER_LENGTH produces a usable border string."""
    border = "-" * INFO_BORDER_LENGTH
    assert_that(border).is_length(INFO_BORDER_LENGTH)
    assert_that(border).is_equal_to("-" * 40)


def test_border_lengths_relationship() -> None:
    """Verify BORDER_LENGTH is longer than INFO_BORDER_LENGTH for visual hierarchy."""
    assert_that(BORDER_LENGTH).is_greater_than(INFO_BORDER_LENGTH)
