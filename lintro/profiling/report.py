"""Build and render the lintro performance profile.

The profile turns the per-tool ``duration_seconds`` captured on each
:class:`~lintro.models.core.tool_result.ToolResult` into a sorted timing
table, a JSON-serializable payload, and optimization suggestions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.formatters.formatter import merge_detected_and_remaining
from lintro.profiling.models import ProfileData, ProfileToolEntry, ToolTiming
from lintro.profiling.suggestions import get_suggestions

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue

# Rounding precision (decimal places) for reported durations.
_DURATION_PRECISION: int = 2


def _merged_issues(result: ToolResult) -> list[BaseIssue]:
    """Return issues using the same merge as JSON ``results[]``.

    Args:
        result: The tool result to inspect.

    Returns:
        Deduplicated detected-plus-remaining issues.
    """
    return merge_detected_and_remaining(
        getattr(result, "initial_issues", None),
        getattr(result, "issues", None),
    )


def _distinct_issue_files(issues: list[BaseIssue]) -> int:
    """Count distinct files a tool reported issues on.

    Args:
        issues: Merged issues for one tool result.

    Returns:
        Number of unique, non-empty file paths across the issues.
    """
    files: set[str] = set()
    for issue in issues:
        file_path = getattr(issue, "file", "")
        if file_path:
            files.add(str(file_path))
    return len(files)


def build_timings(results: list[ToolResult]) -> list[ToolTiming]:
    """Build per-tool timing records, sorted slowest first.

    Only tools that were actually measured are included: skipped tools and
    any result without a captured ``duration`` (e.g. post-checks) are omitted
    so the profile never fabricates timing data. Crashed and timed-out tools
    are included when the executor recorded ``duration_seconds``. Ties are
    broken by tool name for deterministic ordering.

    Args:
        results: Completed tool results from a run.

    Returns:
        Timing records ordered by descending duration.
    """
    timings: list[ToolTiming] = []
    for result in results:
        if getattr(result, "skipped", False):
            continue
        duration = getattr(result, "duration_seconds", None)
        if duration is None:
            continue
        merged = _merged_issues(result)
        timings.append(
            ToolTiming(
                tool=result.name,
                duration=float(duration),
                files_with_issues=_distinct_issue_files(merged),
                issues_found=len(merged),
            ),
        )
    timings.sort(key=lambda t: (-t.duration, t.tool))
    return timings


def build_profile_data(results: list[ToolResult]) -> ProfileData:
    """Build the JSON-serializable profile payload from tool results.

    Args:
        results: Completed tool results from a run.

    Returns:
        A payload with ``cumulative_tool_duration`` (sum of per-tool seconds;
        not wall-clock under parallel execution), a ``tools`` list of
        per-tool objects (``name``, ``duration``, ``files_with_issues``,
        ``issues_found``), and a ``suggestions`` list. ``files_with_issues``
        is distinct ``issue.file`` values, not files scanned.
    """
    timings = build_timings(results)
    total_duration = round(
        sum(t.duration for t in timings),
        _DURATION_PRECISION,
    )
    tools: list[ProfileToolEntry] = [
        {
            "name": t.tool,
            "duration": round(t.duration, _DURATION_PRECISION),
            "files_with_issues": t.files_with_issues,
            "issues_found": t.issues_found,
        }
        for t in timings
    ]
    return {
        "cumulative_tool_duration": total_duration,
        "tools": tools,
        "suggestions": get_suggestions(timings),
    }


def _render_table(timings: list[ToolTiming], total_duration: float) -> list[str]:
    """Render the timing table as a list of box-drawn lines.

    Args:
        timings: Per-tool timing records (already sorted).
        total_duration: Sum of all tool durations in seconds.

    Returns:
        The table rendered as individual text lines.
    """
    headers = ("Tool", "Duration", "Issue files", "Issues")
    rows: list[tuple[str, str, str, str]] = [
        (
            t.tool,
            f"{t.duration:.2f}s",
            str(t.files_with_issues),
            str(t.issues_found),
        )
        for t in timings
    ]
    total_issues = sum(t.issues_found for t in timings)
    total_row = (
        "CUMULATIVE",
        f"{total_duration:.2f}s",
        "",
        str(total_issues),
    )

    # Compute column widths across header, data rows, and the total row.
    all_rows = [headers, *rows, total_row]
    widths = [max(len(row[col]) for row in all_rows) for col in range(len(headers))]

    def _sep(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _row(cells: tuple[str, ...]) -> str:
        padded = [f" {cell.ljust(widths[i])} " for i, cell in enumerate(cells)]
        return "│" + "│".join(padded) + "│"

    lines = [
        _sep("┌", "┬", "┐"),
        _row(headers),
        _sep("├", "┼", "┤"),
    ]
    lines.extend(_row(row) for row in rows)
    lines.append(_sep("├", "┼", "┤"))
    lines.append(_row(total_row))
    lines.append(_sep("└", "┴", "┘"))
    return lines


def render_profile_report(results: list[ToolResult]) -> str:
    """Render the human-readable performance profile report.

    Args:
        results: Completed tool results from a run.

    Returns:
        The full report text, or an empty string when no tools were timed.
    """
    timings = build_timings(results)
    if not timings:
        return ""

    total_duration = round(
        sum(t.duration for t in timings),
        _DURATION_PRECISION,
    )
    lines: list[str] = [
        "Performance Profile",
        "",
        "Tool Timing (sorted by duration):",
        *_render_table(timings, total_duration),
    ]

    suggestions = get_suggestions(timings)
    if suggestions:
        lines.append("")
        lines.append("Suggestions:")
        lines.extend(f"  - {suggestion}" for suggestion in suggestions)

    return "\n".join(lines)
