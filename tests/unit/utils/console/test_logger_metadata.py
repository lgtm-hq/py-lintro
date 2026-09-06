"""Unit tests for ThreadSafeConsoleLogger metadata and pytest result methods.

Tests cover the _print_metadata_messages helper for parsing tool output
and the _print_pytest_results helper for displaying test results.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger

# =============================================================================
# Metadata Message Tests
# =============================================================================


@pytest.mark.parametrize(
    ("raw_output", "expected_substring"),
    [
        pytest.param("5 fixable with ruff", "5 auto-fixable", id="fixable-count"),
        pytest.param("0 fixable issues", "No issues found", id="zero-fixable"),
        pytest.param(
            "Some issues cannot be auto-fixed",
            "cannot be auto-fixed",
            id="unfixable",
        ),
        pytest.param(
            "file.py would reformat",
            "would be reformatted",
            id="would-reformat",
        ),
        pytest.param(
            "3 issues fixed successfully",
            "were fixed",
            id="issues-fixed",
        ),
        pytest.param("some random output", "No issues found", id="random-output"),
    ],
)
def test_print_metadata_messages_patterns(
    logger: ThreadSafeConsoleLogger,
    raw_output: str,
    expected_substring: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify _print_metadata_messages handles various output patterns correctly.

    Different tool output patterns should be recognized and formatted into
    the user-friendly informational message that reaches the terminal.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        raw_output: The raw output to parse for metadata.
        expected_substring: A substring expected in the printed message.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger._print_metadata_messages(raw_output)

    out = capsys.readouterr().out
    assert_that(out).contains(expected_substring)


# =============================================================================
# Pytest Results Tests
# =============================================================================


def test_print_pytest_results_handles_empty_output(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty pytest output still prints the header and the status line.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger._print_pytest_results("", success=True)

    out = capsys.readouterr().out
    assert_that(out).contains("Test Results")
    assert_that(out).contains("All tests passed")


@pytest.mark.parametrize(
    ("success", "expected_status"),
    [
        pytest.param(True, "All tests passed", id="success"),
        pytest.param(False, "Some tests failed", id="failure"),
    ],
)
def test_print_pytest_results_both_outcomes(
    logger: ThreadSafeConsoleLogger,
    success: bool,
    expected_status: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each outcome prints its own status line above the captured output.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        success: Whether the test run was successful.
        expected_status: Status line the outcome must print.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger._print_pytest_results("output", success=success)

    out = capsys.readouterr().out
    assert_that(out).contains(expected_status)
    assert_that(out).contains("output")
