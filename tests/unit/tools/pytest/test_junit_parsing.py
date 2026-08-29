"""Tests for pytest JUnit XML output parsing."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_parser import parse_pytest_junit_xml

# =============================================================================
# Tests for parse_pytest_junit_xml function
# =============================================================================


def test_parse_junit_success_returns_empty(
    mock_test_junit_xml_success: str,
) -> None:
    """Parse JUnit XML with all passed returns empty list.

    Args:
        mock_test_junit_xml_success: Mock JUnit XML string for successful tests.
    """
    issues = parse_pytest_junit_xml(mock_test_junit_xml_success)
    assert_that(issues).is_empty()


def test_parse_junit_failure_returns_issues(
    mock_test_junit_xml_failure: str,
) -> None:
    """Parse JUnit XML with failure returns issue list.

    Args:
        mock_test_junit_xml_failure: Mock JUnit XML string for failed tests.
    """
    issues = parse_pytest_junit_xml(mock_test_junit_xml_failure)
    assert_that(issues).is_length(1)
    assert_that(issues[0].test_status).is_equal_to("FAILED")
    assert_that(issues[0].test_name).is_equal_to("test_failure")


def test_parse_junit_mixed_returns_all_issues(
    mock_test_junit_xml_mixed: str,
) -> None:
    """Parse JUnit XML with mixed results returns all non-passing issues.

    Args:
        mock_test_junit_xml_mixed: Mock JUnit XML string with mixed test results.
    """
    issues = parse_pytest_junit_xml(mock_test_junit_xml_mixed)
    assert_that(issues).is_length(3)  # failed, error, skipped
    statuses = {issue.test_status for issue in issues}
    assert_that(statuses).contains("FAILED", "ERROR", "SKIPPED")


def test_parse_junit_empty_returns_empty() -> None:
    """Parse empty JUnit XML returns empty list."""
    issues = parse_pytest_junit_xml("")
    assert_that(issues).is_empty()
