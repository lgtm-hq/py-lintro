"""Tests for PytestResultProcessor class."""

from __future__ import annotations

from typing import cast

from assertpy import assert_that

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.tools.implementations.pytest.pytest_result_processor import (
    PytestResultProcessor,
)

# =============================================================================
# Tests for PytestResultProcessor class
# =============================================================================


def test_build_result_success(
    result_processor: PytestResultProcessor,
) -> None:
    """Build result for successful test run.

    Args:
        result_processor: PytestResultProcessor instance for testing.
    """
    summary_data = {
        "passed": 10,
        "failed": 0,
        "skipped": 0,
        "error": 0,
        "duration": 0.12,
        "total": 10,
    }
    result = result_processor.build_result(
        success=True,
        summary_data=summary_data,
        all_issues=[],
    )
    assert_that(result.success).is_true()
    assert_that(result.name).is_equal_to("pytest")
    assert_that(result.issues_count).is_equal_to(0)


def test_build_result_failure(
    result_processor: PytestResultProcessor,
    sample_pytest_issues: list[PytestIssue],
) -> None:
    """Build result for failed test run.

    Args:
        result_processor: PytestResultProcessor instance for testing.
        sample_pytest_issues: List of sample pytest issues for testing.
    """
    summary_data = {
        "passed": 7,
        "failed": 2,
        "skipped": 1,
        "error": 1,
        "duration": 1.50,
        "total": 10,
    }
    result = result_processor.build_result(
        success=False,
        summary_data=summary_data,
        all_issues=sample_pytest_issues,
    )
    assert_that(result.success).is_false()
    # Only FAILED and ERROR issues should be counted
    assert_that(result.issues_count).is_equal_to(2)


def test_build_result_filters_skipped(
    result_processor: PytestResultProcessor,
    sample_pytest_issues: list[PytestIssue],
) -> None:
    """Build result filters out SKIPPED from issues.

    Args:
        result_processor: PytestResultProcessor instance for testing.
        sample_pytest_issues: List of sample pytest issues for testing.
    """
    summary_data = {
        "passed": 7,
        "failed": 1,
        "skipped": 1,
        "error": 1,
        "duration": 1.50,
        "total": 10,
    }
    result = result_processor.build_result(
        success=False,
        summary_data=summary_data,
        all_issues=sample_pytest_issues,
    )
    # SKIPPED issues should not be in the result.issues
    assert_that(result.issues).is_not_none()
    issue_statuses = {
        cast(PytestIssue, issue).test_status for issue in result.issues  # type: ignore[union-attr]
    }
    assert_that(issue_statuses).does_not_contain("SKIPPED")


def test_build_result_has_pytest_summary(
    result_processor: PytestResultProcessor,
) -> None:
    """Build result includes pytest_summary.

    Args:
        result_processor: PytestResultProcessor instance for testing.
    """
    summary_data = {
        "passed": 10,
        "failed": 0,
        "skipped": 0,
        "error": 0,
        "duration": 0.12,
        "total": 10,
    }
    result = result_processor.build_result(
        success=True,
        summary_data=summary_data,
        all_issues=[],
    )
    assert_that(result.pytest_summary).is_not_none()
    assert_that(result.pytest_summary.get("passed")).is_equal_to(10)  # type: ignore[union-attr]
