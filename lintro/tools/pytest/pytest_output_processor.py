"""Output processing functions for pytest tool.

This module contains output parsing, summary extraction, performance warnings,
and flaky test detection logic extracted from PytestTool to improve
maintainability and reduce file size.

All functions are re-exported from submodules for backwards compatibility.
"""

from __future__ import annotations

from lintro.tools.pytest.coverage_processor import (
    extract_coverage_report,
    parse_coverage_summary,
)
from lintro.tools.pytest.formatters import (
    _extract_brief_message,
    build_output_with_failures,
    format_pytest_issue,
    format_pytest_issues_table,
    process_test_summary,
)
from lintro.tools.pytest.output_parsers import (
    parse_pytest_output_with_fallback,
)
from lintro.tools.pytest.test_analytics import (
    PYTEST_FLAKY_FAILURE_RATE,
    PYTEST_FLAKY_MIN_RUNS,
    PYTEST_SLOW_TEST_THRESHOLD,
    PYTEST_TOTAL_TIME_WARNING,
    check_total_time_warning,
    detect_and_log_flaky_tests,
    detect_and_log_slow_tests,
)

__all__ = [
    # Constants
    "PYTEST_FLAKY_FAILURE_RATE",
    "PYTEST_FLAKY_MIN_RUNS",
    "PYTEST_SLOW_TEST_THRESHOLD",
    "PYTEST_TOTAL_TIME_WARNING",
    # Output parsing
    "parse_pytest_output_with_fallback",
    # Test analytics
    "check_total_time_warning",
    "detect_and_log_flaky_tests",
    "detect_and_log_slow_tests",
    # Coverage processing
    "extract_coverage_report",
    "parse_coverage_summary",
    # Formatters
    "_extract_brief_message",
    "build_output_with_failures",
    "format_pytest_issue",
    "format_pytest_issues_table",
    "process_test_summary",
]
