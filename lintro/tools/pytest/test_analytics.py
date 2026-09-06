"""Test analytics for pytest: slow and flaky test detection.

This module provides functions for detecting slow and flaky tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.tools.pytest.collection import (
    compute_updated_flaky_test_history,
    extract_all_test_results_from_junit,
    is_ci_environment,
    save_flaky_test_history,
)
from lintro.tools.pytest.output import detect_flaky_tests

# Constants for pytest configuration
PYTEST_SLOW_TEST_THRESHOLD: float = 1.0  # Warn if any test takes > 1 second
PYTEST_TOTAL_TIME_WARNING: float = 60.0  # Warn if total execution time > 60 seconds
PYTEST_FLAKY_MIN_RUNS: int = 3  # Minimum runs before detecting flaky tests
PYTEST_FLAKY_FAILURE_RATE: float = 0.3  # Consider flaky if fails >= 30% but < 100%


def detect_and_log_slow_tests(
    issues: list[PytestIssue],
    options: dict[str, Any],
) -> list[tuple[str, float]]:
    """Detect slow tests and log warnings.

    Args:
        issues: List of parsed test issues.
        options: Options dictionary.

    Returns:
        list[tuple[str, float]]: List of (test_name, duration) tuples for slow tests.
    """
    slow_tests: list[tuple[str, float]] = []
    # Check all issues (including passed tests) for slow tests
    if issues:
        # Find slow tests (individual test duration > threshold)
        slow_threshold = options.get(
            "slow_test_threshold",
            PYTEST_SLOW_TEST_THRESHOLD,
        )
        for issue in issues:
            if (
                issue.duration
                and isinstance(issue.duration, (int, float))
                and issue.duration > slow_threshold
            ):
                slow_tests.append((issue.test_name, issue.duration))

    # Log slow test files
    if slow_tests:
        # Sort by duration descending
        slow_tests.sort(key=lambda x: x[1], reverse=True)
        slow_threshold = options.get(
            "slow_test_threshold",
            PYTEST_SLOW_TEST_THRESHOLD,
        )
        slow_msg = f"Found {len(slow_tests)} slow test(s) (> {slow_threshold}s):"
        logger.info(slow_msg)
        for test_name, duration in slow_tests[:10]:  # Show top 10 slowest
            logger.info(f"  - {test_name}: {duration:.2f}s")
        if len(slow_tests) > 10:
            logger.info(f"  ... and {len(slow_tests) - 10} more")

    return slow_tests


def check_total_time_warning(
    summary_duration: float,
    options: dict[str, Any],
) -> None:
    """Check and warn if total execution time exceeds threshold.

    Args:
        summary_duration: Total test execution duration.
        options: Options dictionary.
    """
    total_time_warning = options.get(
        "total_time_warning",
        PYTEST_TOTAL_TIME_WARNING,
    )
    if summary_duration > total_time_warning:
        warning_msg = (
            f"Tests took {summary_duration:.1f}s to run "
            f"(threshold: {total_time_warning}s). "
            "Consider optimizing slow tests."
        )
        logger.warning(warning_msg)


def detect_and_log_flaky_tests(
    issues: list[PytestIssue],
    options: dict[str, Any],
) -> list[tuple[str, float]]:
    """Detect flaky tests and log warnings.

    Args:
        issues: List of parsed test issues.
        options: Options dictionary.

    Returns:
        list[tuple[str, float]]: List of (node_id, failure_rate) tuples for flaky tests.
    """
    enable_flaky_detection = options.get("detect_flaky", True)
    flaky_tests: list[tuple[str, float]] = []
    if enable_flaky_detection:
        # Try to get all test results from JUnit XML if available
        all_test_results: dict[str, str] | None = None
        junitxml_path = options.get("junitxml") or (
            "report.xml" if is_ci_environment() else None
        )
        if junitxml_path and Path(junitxml_path).exists():
            all_test_results = extract_all_test_results_from_junit(
                junitxml_path,
            )

        # Update flaky test history
        history = compute_updated_flaky_test_history(issues, all_test_results)
        save_flaky_test_history(history)

        # Detect flaky tests
        min_runs = options.get("flaky_min_runs", PYTEST_FLAKY_MIN_RUNS)
        failure_rate = options.get(
            "flaky_failure_rate",
            PYTEST_FLAKY_FAILURE_RATE,
        )
        flaky_tests = detect_flaky_tests(history, min_runs, failure_rate)

        # Report flaky tests
        if flaky_tests:
            flaky_msg = f"Found {len(flaky_tests)} potentially flaky test(s):"
            logger.warning(flaky_msg)
            for node_id, rate in flaky_tests[:10]:  # Show top 10 flakiest
                logger.warning(
                    f"  - {node_id}: {rate:.0%} failure rate "
                    f"({history[node_id]['failed'] + history[node_id]['error']}"
                    f" failures in {sum(history[node_id].values())} runs)",
                )
            if len(flaky_tests) > 10:
                logger.warning(f"  ... and {len(flaky_tests) - 10} more")

    return flaky_tests
