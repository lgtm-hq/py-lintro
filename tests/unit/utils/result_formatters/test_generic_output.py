"""Tests for generic tool output in print_tool_result."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.result_formatters import print_tool_result

if TYPE_CHECKING:
    from collections.abc import Callable


def test_no_issues_shows_success_message(
    console_capture_with_kwargs: tuple[
        Callable[..., None],
        list[tuple[str, dict[str, Any]]],
    ],
    success_capture: tuple[Callable[[str], None], list[str]],
) -> None:
    """Verify success message displayed when tool finds no issues.

    Args:
        console_capture_with_kwargs: Mock console output capture with kwargs.
        success_capture: Mock success message capture.
    """
    mock_console, _ = console_capture_with_kwargs
    mock_success, success_calls = success_capture

    print_tool_result(
        console_output_func=mock_console,
        success_func=mock_success,
        tool_name="ruff",
        output="",
        issues_count=0,
    )

    assert_that(success_calls).contains("✓ No issues found.")


def test_issues_found_shows_error_message_with_count(
    console_capture_with_kwargs: tuple[
        Callable[..., None],
        list[tuple[str, dict[str, Any]]],
    ],
    success_capture: tuple[Callable[[str], None], list[str]],
) -> None:
    """Verify error message with issue count displayed when issues found.

    Args:
        console_capture_with_kwargs: Mock console output capture with kwargs.
        success_capture: Mock success message capture.
    """
    mock_console, console_output = console_capture_with_kwargs
    mock_success, _ = success_capture

    print_tool_result(
        console_output_func=mock_console,
        success_func=mock_success,
        tool_name="ruff",
        output="Some lint errors",
        issues_count=5,
    )

    red_texts = [t for t, kwargs in console_output if kwargs.get("color") == "red"]
    assert_that(any("5 issues" in t for t in red_texts)).is_true()


def test_success_false_shows_failure_even_with_zero_issues(
    console_capture_with_kwargs: tuple[
        Callable[..., None],
        list[tuple[str, dict[str, Any]]],
    ],
    success_capture: tuple[Callable[[str], None], list[str]],
) -> None:
    """Verify failure message shown when success=False despite zero issues.

    Args:
        console_capture_with_kwargs: Mock console output capture with kwargs.
        success_capture: Mock success message capture.
    """
    mock_console, console_output = console_capture_with_kwargs
    mock_success, _ = success_capture

    print_tool_result(
        console_output_func=mock_console,
        success_func=mock_success,
        tool_name="ruff",
        output="",
        issues_count=0,
        success=False,
    )

    red_texts = [t for t, kwargs in console_output if kwargs.get("color") == "red"]
    assert_that(any("failed" in t.lower() for t in red_texts)).is_true()


@pytest.mark.parametrize(
    ("output_text", "description"),
    [
        ("No files to lint", "no files to lint message"),
        ("No Python files found to lint", "no Python files found message"),
        ("", "no display output"),
    ],
    ids=["no_files_to_lint", "no_python_files_found", "silent"],
)
@pytest.mark.parametrize("no_files", [True, False])
def test_no_files_processed_warning(
    console_capture_with_kwargs: tuple[
        Callable[..., None],
        list[tuple[str, dict[str, Any]]],
    ],
    success_capture: tuple[Callable[[str], None], list[str]],
    output_text: str,
    description: str,
    no_files: bool,
) -> None:
    """Only structured empty discovery produces the no-files warning.

    Args:
        console_capture_with_kwargs: Mock console output capture with kwargs.
        success_capture: Mock success message capture.
        output_text: Tool output text to test.
        description: Description of the test case.
        no_files: Whether the producer explicitly reports empty discovery.
    """
    mock_console, console_output = console_capture_with_kwargs
    mock_success, _ = success_capture

    print_tool_result(
        console_output_func=mock_console,
        success_func=mock_success,
        tool_name="ruff",
        output=output_text,
        issues_count=0,
        result=ToolResult(
            name="ruff",
            success=True,
            no_files=no_files,
            inspected_files=False if no_files else None,
        ),
    )

    texts = [t for t, _ in console_output]
    assert_that(any("No files processed" in t for t in texts)).described_as(
        f"Expected 'No files processed' warning for {description}",
    ).is_equal_to(no_files)
