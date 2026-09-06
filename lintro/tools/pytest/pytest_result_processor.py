"""Pytest result processing.

This module contains the PytestResultProcessor class that handles test result
processing, summary generation, and ToolResult building.
"""

from dataclasses import dataclass, field
from typing import Any

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.tools.pytest.pytest_config import PytestConfiguration
from lintro.tools.pytest.pytest_output_processor import (
    build_output_with_failures,
    check_total_time_warning,
    detect_and_log_flaky_tests,
    detect_and_log_slow_tests,
    process_test_summary,
)


@dataclass
class PytestResultProcessor:
    """Handles pytest result processing and ToolResult building.

    This class encapsulates the logic for processing test results, generating
    summaries, and building ToolResult objects from pytest execution data.

    Attributes:
        config: PytestConfiguration instance with result processing options.
        tool_name: Name of the tool (e.g., "pytest").
    """

    config: PytestConfiguration = field(default_factory=PytestConfiguration)
    tool_name: str = field(default="pytest")

    def process_test_results(
        self,
        output: str,
        return_code: int,
        issues: list[PytestIssue],
        total_available_tests: int,
    ) -> tuple[dict[str, Any], list[PytestIssue]]:
        """Process test results and generate summary.

        Args:
            output: Raw output from pytest.
            return_code: Return code from pytest.
            issues: Parsed test issues.
            total_available_tests: Total number of available tests.

        Returns:
            Tuple[Dict, List]: Tuple of (summary_data, all_issues).
        """
        # Process summary
        summary_data = process_test_summary(
            output=output,
            issues=issues,
            total_available_tests=total_available_tests,
        )

        # Performance warnings (uses all issues including passed for duration info)
        detect_and_log_slow_tests(issues, self.config.get_options_dict())
        check_total_time_warning(
            summary_data["duration"],
            self.config.get_options_dict(),
        )

        # Flaky test detection
        detect_and_log_flaky_tests(issues, self.config.get_options_dict())

        # Return all issues - filtering for ToolResult.issues happens in build_result
        return (summary_data, issues)

    def build_result(
        self,
        success: bool,
        summary_data: dict[str, Any],
        all_issues: list[PytestIssue],
        raw_output: str | None = None,
    ) -> ToolResult:
        """Build final ToolResult from processed data.

        Args:
            success: Whether tests passed.
            summary_data: Summary data dictionary.
            all_issues: List of all test issues (failures, errors, skips).
            raw_output: Optional raw pytest output for coverage report extraction.

        Returns:
            ToolResult: Final result object.
        """
        # Filter to only failed/error issues for the ToolResult.issues field
        failed_issues = [
            issue for issue in all_issues if issue.test_status in ("FAILED", "ERROR")
        ]

        output_text = build_output_with_failures(summary_data, all_issues, raw_output)

        result = ToolResult(
            name=self.tool_name,
            success=success,
            issues=failed_issues,
            output=output_text,
            issues_count=len(failed_issues),
        )

        # Store summary data for display in Execution Summary table
        result.pytest_summary = summary_data

        return result
