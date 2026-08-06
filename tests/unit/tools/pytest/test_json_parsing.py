"""Tests for pytest JSON output parsing."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_parser import parse_pytest_json_output

# =============================================================================
# Tests for parse_pytest_json_output function
# =============================================================================


def test_parse_json_success_returns_empty(
    mock_test_json_success: str,
) -> None:
    """Parse JSON with all passed returns empty list.

    Args:
        mock_test_json_success: Mock JSON string for successful tests.
    """
    issues = parse_pytest_json_output(mock_test_json_success)
    # All tests passed, no failure/error/skipped issues
    assert_that(issues).is_empty()


def test_parse_json_failure_returns_issues(
    mock_test_json_failure: str,
) -> None:
    """Parse JSON with failure returns issue list.

    Args:
        mock_test_json_failure: Mock JSON string for failed tests.
    """
    issues = parse_pytest_json_output(mock_test_json_failure)
    assert_that(issues).is_length(1)
    assert_that(issues[0].test_status).is_equal_to("FAILED")
    assert_that(issues[0].test_name).is_equal_to("test_failure")


def test_parse_json_mixed_returns_all_issues(
    mock_test_json_mixed: str,
) -> None:
    """Parse JSON with mixed results returns all non-passing issues.

    Args:
        mock_test_json_mixed: Mock JSON string for mixed test results.
    """
    issues = parse_pytest_json_output(mock_test_json_mixed)
    assert_that(issues).is_length(3)  # failed, error, skipped
    statuses = {issue.test_status for issue in issues}
    assert_that(statuses).contains("FAILED", "ERROR", "SKIPPED")


def test_parse_json_empty_returns_empty() -> None:
    """Parse empty JSON returns empty list."""
    issues = parse_pytest_json_output("")
    assert_that(issues).is_empty()


def test_parse_json_invalid_returns_empty() -> None:
    """Parse invalid JSON returns empty list."""
    issues = parse_pytest_json_output("not valid json")
    assert_that(issues).is_empty()
