"""Tests for pytest output processing functions."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.tools.pytest.pytest_output_processor import (
    FAILURE_SECTION_MAX_CHARS,
    build_output_with_failures,
    detect_and_log_slow_tests,
    extract_failure_section,
    parse_pytest_output_with_fallback,
    process_test_summary,
)

# =============================================================================
# Tests for process_test_summary function
# =============================================================================


def test_process_test_summary_all_passed(
    mock_test_success_output: str,
) -> None:
    """Process summary for all passed tests.

    Args:
        mock_test_success_output: Mock pytest output with all tests passing.
    """
    summary = process_test_summary(
        output=mock_test_success_output,
        issues=[],
        total_available_tests=10,
    )
    assert_that(summary["passed"]).is_equal_to(10)
    assert_that(summary["failed"]).is_equal_to(0)


def test_process_test_summary_with_failures(
    mock_test_failure_output: str,
    sample_pytest_issues: list[PytestIssue],
) -> None:
    """Process summary with failures.

    Args:
        mock_test_failure_output: Mock pytest output with test failures.
        sample_pytest_issues: List of sample pytest issues for testing.
    """
    # Filter to only FAILED/ERROR for the summary calculation
    failed_issues = [
        i for i in sample_pytest_issues if i.test_status in ("FAILED", "ERROR")
    ]
    summary = process_test_summary(
        output=mock_test_failure_output,
        issues=failed_issues,
        total_available_tests=10,
    )
    assert_that(summary["failed"]).is_equal_to(2)


# =============================================================================
# Tests for detect_and_log_slow_tests function
# =============================================================================


def test_detect_slow_tests_finds_slow() -> None:
    """Detect slow tests when duration exceeds threshold."""
    issues = [
        PytestIssue(
            file="test.py",
            line=1,
            test_name="test_slow",
            message="",
            test_status="PASSED",
            duration=5.0,
        ),
    ]
    slow_tests = detect_and_log_slow_tests(issues, {"slow_test_threshold": 1.0})
    assert_that(slow_tests).is_length(1)
    assert_that(slow_tests[0][0]).is_equal_to("test_slow")
    assert_that(slow_tests[0][1]).is_equal_to(5.0)


def test_detect_slow_tests_none_slow() -> None:
    """Detect no slow tests when all are fast."""
    issues = [
        PytestIssue(
            file="test.py",
            line=1,
            test_name="test_fast",
            message="",
            test_status="PASSED",
            duration=0.1,
        ),
    ]
    slow_tests = detect_and_log_slow_tests(issues, {"slow_test_threshold": 1.0})
    assert_that(slow_tests).is_empty()


# =============================================================================
# Tests for build_output_with_failures function
# =============================================================================


def test_build_output_with_failures_includes_summary() -> None:
    """Build output includes JSON summary."""
    summary_data = {
        "passed": 10,
        "failed": 0,
        "skipped": 0,
        "error": 0,
        "duration": 0.12,
        "total": 10,
    }
    output = build_output_with_failures(summary_data, [])
    assert_that(output).contains('"passed": 10')


_RAW_PYTEST_OUTPUT = """collected 2 items

=================================== FAILURES ===================================
____________________ test_check_detects_violations _____________________
E   AssertionError: Expected <['typecheck']> to contain <errcheck>
raw output='Error: parallel golangci-lint is running'
=========================== short test summary info ============================
FAILED tests/integration/x.py::test_check_detects_violations
========================= 1 failed, 1 passed in 6.25s ==========================
"""


def test_extract_failure_section_returns_pytest_block() -> None:
    """The raw FAILURES block is returned without the trailing sections."""
    section = extract_failure_section(_RAW_PYTEST_OUTPUT)
    assert_that(section).contains("AssertionError")
    assert_that(section).contains("parallel golangci-lint is running")
    assert_that(section).does_not_contain("short test summary info")


def test_extract_failure_section_empty_without_failures() -> None:
    """Output with no FAILURES banner yields no section."""
    assert_that(extract_failure_section("2 passed in 1.0s")).is_equal_to("")
    assert_that(extract_failure_section(None)).is_equal_to("")


def test_extract_failure_section_truncates_long_output() -> None:
    """A mass failure cannot bury the summary it is appended to."""
    body = "\n".join(f"E   line {i}" for i in range(20_000))
    section = extract_failure_section(f"=== FAILURES ===\n{body}\n")
    assert_that(len(section)).is_less_than_or_equal_to(
        FAILURE_SECTION_MAX_CHARS + 100,
    )
    assert_that(section).contains("truncated by lintro")


def test_build_output_with_failures_appends_raw_failure_section() -> None:
    """A failing run carries pytest's own failure text, not just the table."""
    summary_data = {
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "error": 0,
        "duration": 6.25,
        "total": 2,
    }
    issue = PytestIssue(
        file="tests/integration/x.py",
        line=1,
        test_name="test_check_detects_violations",
        test_status="FAILED",
        message="AssertionError",
    )
    output = build_output_with_failures(
        summary_data,
        [issue],
        _RAW_PYTEST_OUTPUT,
    )
    assert_that(output).contains("parallel golangci-lint is running")


def test_build_output_with_failures_omits_section_when_all_pass() -> None:
    """A green run gains no failure section."""
    summary_data = {
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "error": 0,
        "duration": 1.0,
        "total": 2,
    }
    output = build_output_with_failures(summary_data, [], _RAW_PYTEST_OUTPUT)
    assert_that(output).does_not_contain("FAILURES")


# =============================================================================
# Tests for parse_pytest_output_with_fallback function
# =============================================================================


def test_fallback_returns_empty_for_empty_output() -> None:
    """Return empty list for empty output and no junitxml."""
    issues = parse_pytest_output_with_fallback(
        output="",
        return_code=0,
        options={},
    )
    assert_that(issues).is_empty()


def test_fallback_uses_json_when_option_set(
    mock_test_json_failure: str,
) -> None:
    """Use JSON parsing when json_report option is set.

    Args:
        mock_test_json_failure: Mock pytest JSON output with test failures.
    """
    issues = parse_pytest_output_with_fallback(
        output=mock_test_json_failure,
        return_code=1,
        options={"json_report": True},
    )
    assert_that(issues).is_length(1)


def test_fallback_to_text_when_format_fails() -> None:
    """Fall back to text parsing when primary format fails."""
    # Invalid JSON but valid text output
    output = "FAILED tests/test.py::test_fail - AssertionError"
    issues = parse_pytest_output_with_fallback(
        output=output,
        return_code=1,
        options={"json_report": True},  # Try JSON first but will fail
    )
    # Should fall back to text and find the failure
    assert_that(issues).is_length(1)
