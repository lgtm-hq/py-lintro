"""Output formatters for pytest results.

This module provides functions for formatting pytest output for display.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.parsers.pytest.pytest_parser import extract_pytest_summary
from lintro.tools.pytest.coverage_processor import (
    parse_coverage_summary,
)


def process_test_summary(
    output: str,
    issues: list[PytestIssue],
    total_available_tests: int,
) -> dict[str, Any]:
    """Process test summary and calculate skipped tests.

    Args:
        output: Raw output from pytest.
        issues: Parsed test issues.
        total_available_tests: Total number of available tests.

    Returns:
        dict: Summary data dictionary.
    """
    # Extract summary statistics
    summary = extract_pytest_summary(output)

    # Filter to only failed/error issues for display
    failed_issues = [
        issue for issue in issues if issue.test_status in ("FAILED", "ERROR")
    ]

    # Use actual failed issues count, not summary count
    # (in case parsing is inconsistent)
    actual_failures = len(failed_issues)

    # Calculate actual skipped tests (tests that exist but weren't run)
    # This includes deselected tests that pytest doesn't report in summary
    # Note: summary.error is already counted in actual_failures, so don't double-count
    # Include xfailed and xpassed in collected count as they are tests that ran
    collected_tests = (
        summary.passed
        + actual_failures
        + summary.skipped
        + summary.xfailed
        + summary.xpassed
    )
    actual_skipped = max(0, total_available_tests - collected_tests)

    logger.debug(f"Total available tests: {total_available_tests}")
    logger.debug(f"Collected tests: {collected_tests}")
    logger.debug(
        f"Summary: passed={summary.passed}, "
        f"failed={actual_failures}, "
        f"skipped={summary.skipped}, "
        f"error={summary.error}",
    )
    logger.debug(f"Actual skipped: {actual_skipped}")

    # Use the larger of summary.skipped or actual_skipped
    # (summary.skipped is runtime skips, actual_skipped includes deselected)
    total_skipped = max(summary.skipped, actual_skipped)

    summary_data = {
        "passed": summary.passed,
        # Use actual parsed failures, not regex summary
        "failed": actual_failures,
        "skipped": total_skipped,
        "error": summary.error,
        "duration": summary.duration,
        "total": total_available_tests,
    }

    return summary_data


def format_pytest_issue(issue: PytestIssue) -> str:
    """Format a single pytest issue in a clean, readable format.

    Args:
        issue: PytestIssue to format.

    Returns:
        str: Formatted issue string.
    """
    status = issue.test_status.upper() if issue.test_status else "UNKNOWN"

    # Choose emoji based on status
    if status == "FAILED":
        emoji = "X"
    elif status == "ERROR":
        emoji = "!"
    elif status == "SKIPPED":
        emoji = "-"
    else:
        emoji = "?"

    # Get test identifier - prefer node_id, fall back to test_name
    test_id = issue.node_id or issue.test_name or "unknown test"

    # Format the main line
    lines = [f"{emoji} {status} {test_id}"]

    # Add brief message if available (first meaningful line only)
    if issue.message:
        # Extract the first meaningful line from the message
        msg_lines = issue.message.strip().split("\n")
        brief_msg = None
        for line in msg_lines:
            line = line.strip()
            # Skip empty lines and pytest output markers, look for error message
            is_valid_line = (
                line and not line.startswith(">") and not line.startswith("E ")
            )
            has_error_info = "Error" in line or "assert" in line.lower() or ":" in line
            if is_valid_line and has_error_info:
                brief_msg = line
                break
        # If no good line found, try to find an "E " line (pytest error indicator)
        if not brief_msg:
            for line in msg_lines:
                if line.strip().startswith("E "):
                    brief_msg = line.strip()[2:].strip()  # Remove "E " prefix
                    break
        # Truncate if too long
        if brief_msg:
            if len(brief_msg) > 100:
                brief_msg = brief_msg[:97] + "..."
            lines.append(f"   {brief_msg}")

    return "\n".join(lines)


def _extract_brief_message(message: str | None) -> str:
    """Extract a brief, single-line message from pytest error output.

    Args:
        message: Full error message from pytest.

    Returns:
        str: Brief message suitable for table display.
    """
    if not message:
        return "-"

    msg_lines = message.strip().split("\n")
    brief_msg = None

    # Look for an informative line
    for line in msg_lines:
        line = line.strip()
        # Skip empty lines and pytest output markers
        is_valid_line = line and not line.startswith(">") and not line.startswith("E ")
        has_error_info = "Error" in line or "assert" in line.lower() or ":" in line
        if is_valid_line and has_error_info:
            brief_msg = line
            break

    # If no good line found, try to find an "E " line (pytest error indicator)
    if not brief_msg:
        for line in msg_lines:
            if line.strip().startswith("E "):
                brief_msg = line.strip()[2:].strip()
                break

    # Fall back to first non-empty line
    if not brief_msg:
        for line in msg_lines:
            if line.strip():
                brief_msg = line.strip()
                break

    if not brief_msg:
        return "-"

    # Truncate if too long for table display
    if len(brief_msg) > 60:
        brief_msg = brief_msg[:57] + "..."

    return brief_msg


def format_pytest_issues_table(issues: list[PytestIssue]) -> str:
    """Format pytest issues as a table similar to check command output.

    Args:
        issues: List of PytestIssue objects to format.

    Returns:
        str: Formatted table string.
    """
    if not issues:
        return ""

    try:
        from tabulate import tabulate
    except ImportError:
        # Fall back to simple format if tabulate not available
        return "\n".join(format_pytest_issue(issue) for issue in issues)

    # Build table data
    table_data: list[list[str]] = []
    for issue in issues:
        status = issue.test_status.upper() if issue.test_status else "UNKNOWN"

        # Add status emoji and color
        if status == "FAILED":
            status_display = "\033[91mX FAILED\033[0m"
        elif status == "ERROR":
            status_display = "\033[91m! ERROR\033[0m"
        elif status == "SKIPPED":
            status_display = "\033[93m- SKIPPED\033[0m"
        else:
            status_display = f"? {status}"

        # Get test identifier - prefer node_id, fall back to test_name or file
        test_id = issue.node_id or issue.test_name or issue.file or "-"
        # Shorten long test IDs for display
        if len(test_id) > 70:
            test_id = "..." + test_id[-67:]

        # Get location info from file:line if available
        location = "-"
        if issue.file and issue.line:
            file_short = issue.file
            if len(file_short) > 40:
                file_short = "..." + file_short[-37:]
            location = f"{file_short}:{issue.line}"
        elif issue.file:
            location = issue.file if len(issue.file) <= 45 else "..." + issue.file[-42:]
        elif issue.line:
            location = str(issue.line)

        # Get brief message
        message = _extract_brief_message(issue.message)

        table_data.append([test_id, location, status_display, message])

    # Generate table
    table = tabulate(
        tabular_data=table_data,
        headers=["Test", "Location", "Status", "Message"],
        tablefmt="grid",
        stralign="left",
        disable_numparse=True,
    )

    return table


#: Longest raw pytest failure section reproduced in the output. Long enough for
#: a handful of ``--tb short`` tracebacks, short enough that a mass failure
#: cannot bury the summary it is appended to.
FAILURE_SECTION_MAX_CHARS: int = 20_000

#: Section headers pytest prints after the failure bodies. The failure section
#: runs from the ``FAILURES`` banner up to the first of these.
_FAILURE_SECTION_END_MARKERS: tuple[str, ...] = (
    "= short test summary info =",
    "= warnings summary =",
    "= slowest ",
    "= PASSES =",
    "= ERRORS =",
)


def extract_failure_section(raw_output: str | None) -> str:
    """Return pytest's own ``FAILURES`` block from a captured run.

    The table lintro builds from parsed issues truncates each message to one
    short line, which is enough to see *that* a test failed and never enough to
    see *why*. In the Docker integration job that table is the only artifact in
    the log, so an intermittent failure there could not be diagnosed at all
    (#2391). Reproducing pytest's own section restores the assertion text and
    the captured stdout/stderr of the failing test.

    Args:
        raw_output: Raw pytest output, or None when it was not captured.

    Returns:
        The failure section, truncated to
        :data:`FAILURE_SECTION_MAX_CHARS`, or an empty string when the output
        carries no failure section.
    """
    if not raw_output:
        return ""
    lines = raw_output.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "= FAILURES =" in line),
        None,
    )
    if start is None:
        return ""
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if any(marker in lines[i] for marker in _FAILURE_SECTION_END_MARKERS)
        ),
        len(lines),
    )
    section = "\n".join(lines[start:end]).strip()
    if len(section) > FAILURE_SECTION_MAX_CHARS:
        section = (
            section[:FAILURE_SECTION_MAX_CHARS]
            + "\n... (failure section truncated by lintro)"
        )
    return section


def build_output_with_failures(
    summary_data: dict[str, Any],
    all_issues: list[PytestIssue],
    raw_output: str | None = None,
) -> str:
    """Build output string with summary and test details.

    Args:
        summary_data: Summary data dictionary.
        all_issues: List of all test issues (failures, errors, skips).
        raw_output: Optional raw pytest output to extract coverage report from.

    Returns:
        str: Formatted output string.
    """
    # Extract and add coverage summary to summary_data if present
    if raw_output:
        coverage_summary = parse_coverage_summary(raw_output)
        if coverage_summary:
            summary_data["coverage"] = coverage_summary

    # Build output with summary and test details
    output_lines = [json.dumps(summary_data)]

    # Note: We no longer include the verbose coverage report in output
    # Coverage summary will be displayed by result_formatters.py using summary_data

    # Format test failures/errors as a table (similar to chk command)
    if all_issues:
        # Separate by status for organized output
        failed = [i for i in all_issues if i.test_status == "FAILED"]
        errors = [i for i in all_issues if i.test_status == "ERROR"]
        skipped = [i for i in all_issues if i.test_status == "SKIPPED"]

        # Show failures and errors in a table (most important)
        if failed or errors:
            output_lines.append("")
            table = format_pytest_issues_table(failed + errors)
            if table:
                output_lines.append(table)

        # Show skipped tests in a separate table - only if there are few
        if skipped and len(skipped) <= 10:
            output_lines.append("")
            output_lines.append("Skipped Tests")
            table = format_pytest_issues_table(skipped)
            if table:
                output_lines.append(table)
        elif skipped:
            # Just show count if many skipped
            output_lines.append("")
            output_lines.append(f"- {len(skipped)} tests skipped")

        # The table above truncates every message to one short line, so append
        # pytest's own failure section: without it a CI log carries no
        # assertion text and an intermittent failure cannot be diagnosed
        # (#2391).
        if failed or errors:
            failure_section = extract_failure_section(raw_output)
            if failure_section:
                output_lines.append("")
                output_lines.append(failure_section)

    return "\n".join(output_lines)
