"""Tests for pytest text output parsing."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_parser import parse_pytest_text_output

# =============================================================================
# Tests for parse_pytest_text_output function
# =============================================================================


def test_parse_text_failed_line() -> None:
    """Parse text output with FAILED line."""
    output = "FAILED tests/test_example.py::test_failure - AssertionError"
    issues = parse_pytest_text_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].test_status).is_equal_to("FAILED")
    assert_that(issues[0].test_name).is_equal_to("test_failure")


def test_parse_text_error_line() -> None:
    """Parse text output with ERROR line."""
    output = "ERROR tests/test_example.py::test_error - RuntimeError"
    issues = parse_pytest_text_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].test_status).is_equal_to("ERROR")


def test_parse_text_skipped_line() -> None:
    """Parse text output with SKIPPED line."""
    output = "tests/test_example.py::test_skip SKIPPED (reason)"
    issues = parse_pytest_text_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].test_status).is_equal_to("SKIPPED")


def test_parse_text_empty_returns_empty() -> None:
    """Parse empty text returns empty list."""
    issues = parse_pytest_text_output("")
    assert_that(issues).is_empty()
