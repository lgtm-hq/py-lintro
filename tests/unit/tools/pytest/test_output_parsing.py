"""Tests for pytest output parsing functions."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_parser import (
    extract_pytest_summary,
    parse_pytest_output,
)

# =============================================================================
# Tests for extract_pytest_summary function
# =============================================================================


def test_extract_summary_all_passed(
    mock_test_success_output: str,
) -> None:
    """Extract summary from all-passed output.

    Args:
        mock_test_success_output: Mock pytest output string for successful tests.
    """
    summary = extract_pytest_summary(mock_test_success_output)
    assert_that(summary.passed).is_equal_to(10)
    assert_that(summary.failed).is_equal_to(0)
    assert_that(summary.skipped).is_equal_to(0)
    assert_that(summary.duration).is_close_to(0.12, tolerance=0.01)


def test_extract_summary_with_failures(
    mock_test_failure_output: str,
) -> None:
    """Extract summary from output with failures.

    Args:
        mock_test_failure_output: Mock pytest output string with test failures.
    """
    summary = extract_pytest_summary(mock_test_failure_output)
    assert_that(summary.passed).is_equal_to(9)
    assert_that(summary.failed).is_equal_to(1)
    assert_that(summary.duration).is_close_to(0.15, tolerance=0.01)


def test_extract_summary_mixed_results(
    mock_test_mixed_output: str,
) -> None:
    """Extract summary from mixed results output.

    Args:
        mock_test_mixed_output: Mock pytest output string with mixed test results.
    """
    summary = extract_pytest_summary(mock_test_mixed_output)
    assert_that(summary.passed).is_equal_to(15)
    assert_that(summary.failed).is_equal_to(2)
    assert_that(summary.skipped).is_equal_to(2)
    assert_that(summary.error).is_equal_to(1)
    assert_that(summary.duration).is_close_to(1.50, tolerance=0.01)


def test_extract_summary_empty_output() -> None:
    """Extract summary from empty output returns defaults."""
    summary = extract_pytest_summary("")
    assert_that(summary.passed).is_equal_to(0)
    assert_that(summary.failed).is_equal_to(0)
    assert_that(summary.skipped).is_equal_to(0)
    assert_that(summary.duration).is_equal_to(0.0)


# =============================================================================
# Tests for parse_pytest_output format dispatch
# =============================================================================


def test_dispatch_json_format(mock_test_json_failure: str) -> None:
    """Dispatch to JSON parser when format is json.

    Args:
        mock_test_json_failure: Mock pytest JSON output string with test failures.
    """
    issues = parse_pytest_output(mock_test_json_failure, format="json")
    assert_that(issues).is_length(1)


def test_dispatch_junit_format(mock_test_junit_xml_failure: str) -> None:
    """Dispatch to JUnit parser when format is junit.

    Args:
        mock_test_junit_xml_failure: Mock pytest JUnit XML output string with test failures.
    """
    issues = parse_pytest_output(mock_test_junit_xml_failure, format="junit")
    assert_that(issues).is_length(1)


def test_dispatch_text_format() -> None:
    """Dispatch to text parser when format is text."""
    output = "FAILED tests/test_example.py::test_failure - AssertionError"
    issues = parse_pytest_output(output, format="text")
    assert_that(issues).is_length(1)


def test_default_format_is_text() -> None:
    """Default format is text."""
    output = "FAILED tests/test_example.py::test_failure - AssertionError"
    issues = parse_pytest_output(output)
    assert_that(issues).is_length(1)
