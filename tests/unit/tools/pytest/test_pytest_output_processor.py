"""Tests for lintro.tools.implementations.pytest.pytest_output_processor module."""

from __future__ import annotations

from assertpy import assert_that


def test_pytest_output_processor_exports_constants() -> None:
    """Module exports expected constants."""
    from lintro.tools.implementations.pytest.pytest_output_processor import (
        PYTEST_FLAKY_FAILURE_RATE,
        PYTEST_FLAKY_MIN_RUNS,
        PYTEST_SLOW_TEST_THRESHOLD,
        PYTEST_TOTAL_TIME_WARNING,
    )

    assert_that(PYTEST_SLOW_TEST_THRESHOLD).is_instance_of(float)
    assert_that(PYTEST_TOTAL_TIME_WARNING).is_instance_of(float)
    assert_that(PYTEST_FLAKY_FAILURE_RATE).is_instance_of(float)
    assert_that(PYTEST_FLAKY_MIN_RUNS).is_instance_of(int)


def test_pytest_output_processor_exports_parse_function() -> None:
    """Module exports parse_pytest_output_with_fallback."""
    from lintro.tools.implementations.pytest.pytest_output_processor import (
        parse_pytest_output_with_fallback,
    )

    assert_that(parse_pytest_output_with_fallback).is_not_none()
    assert_that(callable(parse_pytest_output_with_fallback)).is_true()


def test_pytest_output_processor_exports_analytics_functions() -> None:
    """Module exports test analytics functions."""
    from lintro.tools.implementations.pytest.pytest_output_processor import (
        check_total_time_warning,
        detect_and_log_flaky_tests,
        detect_and_log_slow_tests,
    )

    assert_that(callable(check_total_time_warning)).is_true()
    assert_that(callable(detect_and_log_flaky_tests)).is_true()
    assert_that(callable(detect_and_log_slow_tests)).is_true()


def test_pytest_output_processor_exports_coverage_functions() -> None:
    """Module exports coverage processing functions."""
    from lintro.tools.implementations.pytest.pytest_output_processor import (
        extract_coverage_report,
        parse_coverage_summary,
    )

    assert_that(callable(extract_coverage_report)).is_true()
    assert_that(callable(parse_coverage_summary)).is_true()


def test_pytest_output_processor_exports_formatter_functions() -> None:
    """Module exports formatter functions."""
    from lintro.tools.implementations.pytest.pytest_output_processor import (
        build_output_with_failures,
        format_pytest_issue,
        format_pytest_issues_table,
        process_test_summary,
    )

    assert_that(callable(build_output_with_failures)).is_true()
    assert_that(callable(format_pytest_issue)).is_true()
    assert_that(callable(format_pytest_issues_table)).is_true()
    assert_that(callable(process_test_summary)).is_true()


def test_pytest_output_processor_all_attribute() -> None:
    """Module __all__ contains expected exports."""
    from lintro.tools.implementations.pytest import pytest_output_processor

    expected = {
        "PYTEST_FLAKY_FAILURE_RATE",
        "PYTEST_FLAKY_MIN_RUNS",
        "PYTEST_SLOW_TEST_THRESHOLD",
        "PYTEST_TOTAL_TIME_WARNING",
        "parse_pytest_output_with_fallback",
        "check_total_time_warning",
        "detect_and_log_flaky_tests",
        "detect_and_log_slow_tests",
        "extract_coverage_report",
        "parse_coverage_summary",
        "_extract_brief_message",
        "build_output_with_failures",
        "format_pytest_issue",
        "format_pytest_issues_table",
        "process_test_summary",
    }

    assert_that(set(pytest_output_processor.__all__)).is_equal_to(expected)
