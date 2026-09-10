"""Tests for PytestIssue dataclass."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_issue import PytestIssue

# =============================================================================
# Tests for PytestIssue dataclass
# =============================================================================


def test_pytest_issue_creation() -> None:
    """Create PytestIssue with all fields."""
    issue = PytestIssue(
        file="test.py",
        line=10,
        test_name="test_example",
        message="AssertionError",
        test_status="FAILED",
        duration=0.5,
        node_id="test.py::test_example",
    )
    assert_that(issue.file).is_equal_to("test.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.test_name).is_equal_to("test_example")
    assert_that(issue.test_status).is_equal_to("FAILED")
    assert_that(issue.duration).is_equal_to(0.5)
    assert_that(issue.node_id).is_equal_to("test.py::test_example")


def test_pytest_issue_default_values() -> None:
    """PytestIssue has correct default values."""
    issue = PytestIssue()
    assert_that(issue.test_name).is_equal_to("")
    assert_that(issue.test_status).is_equal_to("")
    assert_that(issue.duration).is_none()
    assert_that(issue.node_id).is_none()
