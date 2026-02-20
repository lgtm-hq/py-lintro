"""Summary table generation for Lintro tool output.

Handles formatting and display of execution summary tables with tabulate.
"""

import contextlib
import sys
from collections.abc import Callable, Sequence
from typing import Any

from lintro.enums.action import Action
from lintro.enums.tool_name import ToolName, normalize_tool_name
from lintro.utils.console import (
    RE_CANNOT_AUTOFIX,
    RE_REMAINING_OR_CANNOT,
    get_summary_value,
    get_tool_emoji,
)

# Constants
DEFAULT_REMAINING_COUNT: str = "?"

# ANSI color codes — only emit when stdout is a terminal
_USE_COLOR = sys.stdout.isatty()
_GREEN = "\033[92m" if _USE_COLOR else ""
_RED = "\033[91m" if _USE_COLOR else ""
_YELLOW = "\033[93m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""


def _extract_skip_reason(output: str) -> str:
    """Extract abbreviated skip reason from tool output.

    Skip messages have format: "Skipping {tool}: {error}. Minimum required: ..."

    Args:
        output: The tool output containing the skip message.

    Returns:
        Abbreviated reason string for display in the summary table.
    """
    if ":" in output and ". Minimum" in output:
        colon_idx = output.index(":")
        minimum_idx = output.index(". Minimum")
        # Ensure colon comes before ". Minimum" to get a valid slice
        if colon_idx >= minimum_idx:
            return "SKIPPED"
        start = colon_idx + 1
        end = minimum_idx
        reason = output[start:end].strip()
        # Abbreviate common error messages
        if "Command failed" in reason:
            return "Cmd failed"
        if "Could not parse version" in reason:
            return "No version"
        if "below minimum" in reason:
            return "Outdated"
        if "Failed to run" in reason:
            return "Not found"
        # Truncate if too long
        return reason[:20] if len(reason) > 20 else reason
    return "SKIPPED"


def _safe_cast(
    summary: dict[str, Any],
    key: str,
    default: int | float,
    converter: Callable[[Any], int | float],
) -> int | float:
    """Safely extract and cast a value from a summary dictionary.

    Args:
        summary: Dictionary containing summary data.
        key: Key to extract from summary.
        default: Default value if extraction/conversion fails.
        converter: Function to convert the extracted value (e.g., int, float).

    Returns:
        Converted value or default if extraction/conversion fails.
    """
    try:
        return converter(get_summary_value(summary, key, default))
    except (ValueError, TypeError):
        return default


def _format_tool_display_name(tool_name: str) -> str:
    """Format tool name for display (convert underscores to hyphens).

    Args:
        tool_name: Raw tool name (may contain underscores).

    Returns:
        Display name with hyphens instead of underscores.
    """
    return tool_name.replace("_", "-")


def _get_ai_applied_count(result: object) -> int:
    """Get AI-applied fix count from tool result metadata."""
    ai_metadata = getattr(result, "ai_metadata", None)
    if not isinstance(ai_metadata, dict):
        return 0
    applied_count = ai_metadata.get(
        "applied_count",
        ai_metadata.get("fixed_count", 0),
    )
    if applied_count is None:
        return 0
    try:
        return max(0, int(applied_count))
    except (TypeError, ValueError):
        return 0


def _get_ai_verified_count(result: object) -> int:
    """Get count of AI-applied fixes verified as resolved."""
    ai_metadata = getattr(result, "ai_metadata", None)
    if not isinstance(ai_metadata, dict):
        return 0
    verified_count = ai_metadata.get("verified_count", 0)
    try:
        return max(0, int(verified_count))
    except (TypeError, ValueError):
        return 0


def _get_ai_unverified_count(result: object) -> int:
    """Get count of AI-applied fixes that remain unresolved."""
    ai_metadata = getattr(result, "ai_metadata", None)
    if not isinstance(ai_metadata, dict):
        return 0
    unverified_count = ai_metadata.get("unverified_count", 0)
    try:
        return max(0, int(unverified_count))
    except (TypeError, ValueError):
        return 0


def _is_result_skipped(result: object) -> tuple[bool, str]:
    """Check if a tool result represents a skipped tool.

    Uses the first-class ``skipped`` field if available, falling back to
    legacy output string matching for backward compatibility.

    Args:
        result: Tool result object.

    Returns:
        Tuple of (is_skipped, skip_reason).
    """
    # First-class field (preferred)
    skipped = getattr(result, "skipped", False)
    if skipped:
        skip_reason = getattr(result, "skip_reason", None) or ""
        return True, skip_reason

    # Legacy fallback: match "Skipping {tool}: ..." pattern in output
    tool_name = getattr(result, "name", "unknown")
    result_output = getattr(result, "output", "") or ""
    if (
        result_output
        and isinstance(result_output, str)
        and result_output.lower().startswith(f"skipping {tool_name.lower()}:")
    ):
        return True, _extract_skip_reason(result_output)

    return False, ""


def count_affected_files(tool_results: Sequence[object]) -> int:
    """Count unique file paths with issues across all tool results.

    Args:
        tool_results: Sequence of tool results to inspect.

    Returns:
        Number of unique files that have at least one issue.
    """
    files: set[str] = set()
    for result in tool_results:
        issues = getattr(result, "issues", None)
        if issues:
            for issue in issues:
                file_path = getattr(issue, "file", "")
                if file_path:
                    files.add(str(file_path))
    return len(files)


def print_summary_table(
    console_output_func: Callable[..., None],
    action: Action,
    tool_results: Sequence[object],
) -> None:
    """Print the summary table for the run.

    Args:
        console_output_func: Function to output text to console
        action: The action being performed.
        tool_results: Sequence of tool results.
    """
    try:
        from tabulate import tabulate

        # Sort results alphabetically by tool name for consistent output
        sorted_results = sorted(
            tool_results,
            key=lambda r: getattr(r, "name", "unknown").lower(),
        )

        summary_data: list[list[str]] = []
        for result in sorted_results:
            tool_name: str = getattr(result, "name", "unknown")
            issues_count: int = getattr(result, "issues_count", 0)
            success: bool = getattr(result, "success", True)

            emoji: str = get_tool_emoji(tool_name)
            display_name: str = _format_tool_display_name(tool_name)
            tool_display: str = f"{emoji} {display_name}"

            # Check skip status (first-class field or legacy fallback)
            is_skipped, skip_reason = _is_result_skipped(result)

            # Special handling for pytest/test action
            # Safely check if this is pytest by normalizing the tool name
            is_pytest = False
            with contextlib.suppress(ValueError):
                is_pytest = normalize_tool_name(tool_name) == ToolName.PYTEST

            if action == Action.TEST and is_pytest:
                if is_skipped:
                    summary_data.append(
                        [
                            tool_display,
                            f"{_YELLOW}⏭️  SKIP{_RESET}",
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            f"{_YELLOW}{skip_reason}{_RESET}" if skip_reason else "",
                        ],
                    )
                    continue

                pytest_summary = getattr(result, "pytest_summary", None)
                if pytest_summary:
                    # Use pytest summary data for more detailed display
                    passed = _safe_cast(pytest_summary, "passed", 0, int)
                    failed = _safe_cast(pytest_summary, "failed", 0, int)
                    skipped_count = _safe_cast(pytest_summary, "skipped", 0, int)
                    duration = _safe_cast(pytest_summary, "duration", 0.0, float)
                    total = _safe_cast(pytest_summary, "total", 0, int)

                    # Create detailed status display
                    status_display = (
                        f"{_GREEN}✅ PASS{_RESET}"
                        if failed == 0
                        else f"{_RED}❌ FAIL{_RESET}"
                    )

                    # Format duration with proper units
                    duration_str = f"{duration:.2f}s"

                    # Create row with separate columns for each metric
                    summary_data.append(
                        [
                            tool_display,
                            status_display,
                            str(passed),
                            str(failed),
                            str(skipped_count),
                            str(total),
                            duration_str,
                            "",  # Notes
                        ],
                    )
                    continue

            # Handle TEST action for non-pytest tools
            if action == Action.TEST:
                if is_skipped:
                    summary_data.append(
                        [
                            tool_display,
                            f"{_YELLOW}⏭️  SKIP{_RESET}",
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            f"{_YELLOW}{skip_reason}{_RESET}" if skip_reason else "",
                        ],
                    )
                    continue

                # Non-pytest tool in test mode - show basic pass/fail
                status_display = (
                    f"{_GREEN}✅ PASS{_RESET}"
                    if (success and issues_count == 0)
                    else f"{_RED}❌ FAIL{_RESET}"
                )
                summary_data.append(
                    [
                        tool_display,
                        status_display,
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "",  # Notes
                    ],
                )
                continue

            # For format operations, success means tool ran
            # (regardless of fixes made)
            # For check operations, success means no issues found
            if action == Action.FIX:
                if is_skipped:
                    summary_data.append(
                        [
                            tool_display,
                            f"{_YELLOW}⏭️  SKIP{_RESET}",
                            "-",  # Fixed
                            "-",  # AI-Applied
                            "-",  # AI-Verified
                            "-",  # Remaining
                            f"{_YELLOW}{skip_reason}{_RESET}" if skip_reason else "",
                        ],
                    )
                    continue

                # Format operations: show fixed count and remaining status
                if success:
                    status_display = f"{_GREEN}✅ PASS{_RESET}"
                else:
                    status_display = f"{_RED}❌ FAIL{_RESET}"

                # Get result output for parsing
                result_output: str = getattr(result, "output", "")

                # Prefer standardized counts from ToolResult
                remaining_std = getattr(result, "remaining_issues_count", None)
                fixed_std = getattr(result, "fixed_issues_count", None)

                if remaining_std is not None:
                    try:
                        remaining_count: int | str = int(remaining_std)
                    except (ValueError, TypeError):
                        remaining_count = DEFAULT_REMAINING_COUNT
                else:
                    # Parse output to determine remaining issues
                    remaining_count = 0
                    if result_output and (
                        "remaining" in result_output.lower()
                        or "cannot be auto-fixed" in result_output.lower()
                    ):
                        remaining_match = RE_CANNOT_AUTOFIX.search(
                            result_output,
                        )
                        if not remaining_match:
                            remaining_match = RE_REMAINING_OR_CANNOT.search(
                                result_output.lower(),
                            )
                        if remaining_match:
                            try:
                                remaining_count = int(remaining_match.group(1))
                            except (ValueError, TypeError):
                                remaining_count = DEFAULT_REMAINING_COUNT
                        elif not success:
                            remaining_count = DEFAULT_REMAINING_COUNT

                if fixed_std is not None:
                    try:
                        fixed_display_value = int(fixed_std)
                    except (ValueError, TypeError):
                        fixed_display_value = 0
                else:
                    try:
                        fixed_display_value = int(issues_count)
                    except (ValueError, TypeError):
                        fixed_display_value = 0

                # Fixed issues display
                fixed_display: str = f"{_GREEN}{fixed_display_value}{_RESET}"
                ai_applied_value = _get_ai_applied_count(result)
                ai_applied_display: str = f"{_GREEN}{ai_applied_value}{_RESET}"
                ai_verified_value = _get_ai_verified_count(result)
                ai_verified_display: str = f"{_GREEN}{ai_verified_value}{_RESET}"
                ai_unverified_value = _get_ai_unverified_count(result)
                notes_display = (
                    f"{_YELLOW}{ai_unverified_value} unverified{_RESET}"
                    if ai_unverified_value > 0
                    else ""
                )

                # Remaining issues display
                if isinstance(remaining_count, str):
                    remaining_display: str = f"{_YELLOW}{remaining_count}{_RESET}"
                else:
                    remaining_display = (
                        f"{_RED}{remaining_count}{_RESET}"
                        if remaining_count > 0
                        else f"{_GREEN}{remaining_count}{_RESET}"
                    )

                summary_data.append(
                    [
                        tool_display,
                        status_display,
                        fixed_display,
                        ai_applied_display,
                        ai_verified_display,
                        remaining_display,
                        notes_display,
                    ],
                )
            else:  # check
                if is_skipped:
                    summary_data.append(
                        [
                            tool_display,
                            f"{_YELLOW}⏭️  SKIP{_RESET}",
                            "-",  # Issues
                            f"{_YELLOW}{skip_reason}{_RESET}" if skip_reason else "",
                        ],
                    )
                    continue

                # Check if this is an execution failure (timeout/error)
                result_output = getattr(result, "output", "") or ""

                has_execution_failure = result_output and (
                    "timeout" in result_output.lower()
                    or "error processing" in result_output.lower()
                    or "tool execution failed" in result_output.lower()
                )

                notes_display = ""

                # Check for framework deferral pattern in output
                if (
                    result_output
                    and result_output.startswith("SKIPPED:")
                    and "detected" in result_output
                ):
                    notes_display = f"{_YELLOW}deferred to framework checker{_RESET}"

                if (has_execution_failure and issues_count == 0) or (
                    not success and issues_count == 0
                ):
                    status_display = f"{_RED}❌ FAIL{_RESET}"
                    issues_display = f"{_RED}ERROR{_RESET}"
                else:
                    status_display = (
                        f"{_GREEN}✅ PASS{_RESET}"
                        if (success and issues_count == 0)
                        else f"{_RED}❌ FAIL{_RESET}"
                    )
                    issues_display = (
                        f"{_GREEN}{issues_count}{_RESET}"
                        if issues_count == 0
                        else f"{_RED}{issues_count}{_RESET}"
                    )

                summary_data.append(
                    [
                        tool_display,
                        status_display,
                        issues_display,
                        notes_display,
                    ],
                )

        # Set headers based on action
        # Use plain headers to avoid ANSI/emojis width misalignment
        headers: list[str]
        if action == Action.TEST:
            headers = [
                "Tool",
                "Status",
                "Passed",
                "Failed",
                "Skipped",
                "Total",
                "Duration",
                "Notes",
            ]
        elif action == Action.FIX:
            headers = [
                "Tool",
                "Status",
                "Fixed",
                "AI-Applied",
                "AI-Verified",
                "Remaining",
                "Notes",
            ]
        else:
            headers = ["Tool", "Status", "Issues", "Notes"]

        # Render with plain values to ensure proper alignment across terminals
        table: str = tabulate(
            tabular_data=summary_data,
            headers=headers,
            tablefmt="grid",
            stralign="left",
            disable_numparse=True,
        )
        console_output_func(text=table)
        console_output_func(text="")

    except ImportError:
        # Fallback if tabulate not available
        console_output_func(text="Summary table requires tabulate package")


def print_totals_table(
    console_output_func: Callable[..., None],
    action: Action,
    total_issues: int = 0,
    total_fixed: int = 0,
    total_remaining: int = 0,
    affected_files: int = 0,
    severity_errors: int = 0,
    severity_warnings: int = 0,
    severity_info: int = 0,
    total_ai_applied: int = 0,
    total_ai_verified: int = 0,
) -> None:
    """Print a totals summary table for the run.

    Args:
        console_output_func: Function to output text to console.
        action: The action being performed.
        total_issues: Total number of issues found (CHECK/TEST mode).
        total_fixed: Total number of native-tool issues fixed (FIX mode).
        total_remaining: Total number of remaining issues (FIX mode).
        affected_files: Number of unique files with issues.
        severity_errors: Number of issues at ERROR severity.
        severity_warnings: Number of issues at WARNING severity.
        severity_info: Number of issues at INFO severity.
        total_ai_applied: Total number of AI-applied fixes (FIX mode).
        total_ai_verified: Total number of AI-verified fixes (FIX mode).
    """
    try:
        import click
        from tabulate import tabulate

        header: str = click.style("\U0001f4ca TOTALS", fg="cyan", bold=True)
        console_output_func(text=header)

        if action == Action.FIX:
            total_resolved = total_fixed + total_ai_verified
            rows: list[list[str | int]] = [
                ["Fixed Issues (Native)", total_fixed],
                ["AI Applied Fixes", total_ai_applied],
                ["AI Verified Fixes", total_ai_verified],
                ["Total Resolved", total_resolved],
                ["Remaining Issues", total_remaining],
                ["Affected Files", affected_files],
            ]
        else:
            rows = [
                ["Total Issues", total_issues],
            ]
            if total_issues > 0:
                rows.append(["  Errors", severity_errors])
                rows.append(["  Warnings", severity_warnings])
                rows.append(["  Info", severity_info])
            rows.append(["Affected Files", affected_files])

        headers: list[str] = ["Metric", "Count"]
        table: str = tabulate(
            tabular_data=rows,
            headers=headers,
            tablefmt="grid",
            stralign="left",
            disable_numparse=True,
        )
        console_output_func(text=table)
        console_output_func(text="")

    except ImportError:
        # Fallback if tabulate not available
        console_output_func(text="Totals table requires tabulate package")
