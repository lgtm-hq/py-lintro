"""Performance optimization suggestions derived from tool timings.

Suggestions are intentionally conservative: they only reference tools that
actually ran in the current invocation, so users never see advice that does
not apply to their setup.
"""

from __future__ import annotations

from lintro.profiling.timer import ToolTiming

# Default threshold (seconds) above which a tool is flagged as slow.
DEFAULT_SLOW_THRESHOLD: float = 5.0

# Tool-specific optimization hints, keyed by tool name (lowercase).
_TOOL_HINTS: dict[str, str] = {
    "mypy": "mypy: consider incremental mode or the mypy daemon (dmypy)",
    "darglint": (
        "darglint is deprecated and slow; consider replacing it with pydoclint"
    ),
    "pylint": "pylint: consider running with --jobs to parallelize analysis",
}


def get_suggestions(
    timings: list[ToolTiming],
    *,
    slow_threshold: float = DEFAULT_SLOW_THRESHOLD,
) -> list[str]:
    """Build human-readable optimization suggestions from tool timings.

    The suggestions are ordered as: the slowest-tool summary first, then any
    tool-specific hints for tools that ran, then per-tool slow-threshold
    warnings for the remaining tools.

    Args:
        timings: Per-tool timing records for the run.
        slow_threshold: Seconds above which a non-slowest tool is flagged.

    Returns:
        A list of suggestion strings (empty when there is nothing to suggest).
    """
    suggestions: list[str] = []
    if not timings:
        return suggestions

    total = sum(t.duration for t in timings)
    slowest = max(timings, key=lambda t: t.duration)

    if total > 0 and slowest.duration > 0:
        pct = round(slowest.duration / total * 100)
        suggestions.append(
            f"{slowest.tool} is slowest ({pct}% of total time)",
        )

    # Tool-specific hints, in timing order, for tools that actually ran.
    for timing in timings:
        hint = _TOOL_HINTS.get(timing.tool.lower())
        if hint is not None:
            suggestions.append(hint)

    # Slow-threshold warnings for tools other than the slowest.
    for timing in timings:
        if timing is slowest:
            continue
        if timing.duration >= slow_threshold:
            suggestions.append(
                f"{timing.tool} took {timing.duration:.2f}s "
                f"(over the {slow_threshold:.0f}s threshold)",
            )

    return suggestions
