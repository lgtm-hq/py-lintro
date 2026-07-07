"""Build and render the lintro performance profile.

The profile turns the per-tool ``duration`` captured on each
:class:`~lintro.models.core.tool_result.ToolResult` into a sorted timing
table, a JSON-serializable payload, and optimization suggestions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.profiling.suggestions import get_suggestions
from lintro.profiling.timer import ToolTiming

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

# Rounding precision (decimal places) for reported durations.
_DURATION_PRECISION: int = 2


def _distinct_issue_files(result: ToolResult) -> int:
    """Count distinct files a tool reported issues on.

    Args:
        result: The tool result to inspect.

    Returns:
        Number of unique, non-empty file paths across the result's issues.
    """
    issues = getattr(result, "issues", None)
    if not issues:
        return 0
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
    so the profile never fabricates timing data. Ties are broken by tool name
    for deterministic ordering.

    Args:
        results: Completed tool results from a run.

    Returns:
        Timing records ordered by descending duration.
    """
    timings: list[ToolTiming] = []
    for result in results:
        if getattr(result, "skipped", False):
            continue
        duration = getattr(result, "duration", None)
        if duration is None:
            continue
        timings.append(
            ToolTiming(
                tool=result.name,
                duration=float(duration),
                files_checked=_distinct_issue_files(result),
                issues_found=int(getattr(result, "issues_count", 0) or 0),
            ),
        )
    timings.sort(key=lambda t: (-t.duration, t.tool))
    return timings


def build_profile_data(results: list[ToolResult]) -> dict[str, Any]:
    """Build the JSON-serializable profile payload from tool results.

    Args:
        results: Completed tool results from a run.

    Returns:
        A dict with ``total_duration`` (seconds), a ``tools`` list of
        per-tool objects (``name``, ``duration``, ``files_checked``,
        ``issues_found``), and a ``suggestions`` list.
    """
    timings = build_timings(results)
    total_duration = round(
        sum(t.duration for t in timings),
        _DURATION_PRECISION,
    )
    return {
        "total_duration": total_duration,
        "tools": [
            {
                "name": t.tool,
                "duration": round(t.duration, _DURATION_PRECISION),
                "files_checked": t.files_checked,
                "issues_found": t.issues_found,
            }
            for t in timings
        ],
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
    headers = ("Tool", "Duration", "Files", "Issues")
    rows: list[tuple[str, str, str, str]] = [
        (
            t.tool,
            f"{t.duration:.2f}s",
            str(t.files_checked),
            str(t.issues_found),
        )
        for t in timings
    ]
    total_issues = sum(t.issues_found for t in timings)
    total_row = (
        "TOTAL",
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
