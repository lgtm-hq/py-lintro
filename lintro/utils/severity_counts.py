"""Tally issue severities and render the counts and their run-over-run delta.

Together with :mod:`lintro.utils.severity_baseline` this is what a check run
reports instead of the 0-100 health score deleted in issue #1739: what was
found, and how it changed since the previous run in the same workspace.

Colour follows the *direction of improvement*, not the arithmetic sign —
fewer issues is better, so ``-12 errors`` is green and ``+3 errors`` is red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.severity_level import SeverityLevel
from lintro.models.core.severity_counts import SeverityCounts, SeverityDelta

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "COLOR_BETTER",
    "COLOR_UNCHANGED",
    "COLOR_WORSE",
    "count_severities",
    "counts_color",
    "delta_color",
    "format_counts_line",
    "format_delta_line",
]

# Console colours. Improvement is green whatever the arithmetic sign, because
# a negative issue delta is the good direction.
COLOR_BETTER: str = "green"
COLOR_WORSE: str = "red"
COLOR_UNCHANGED: str = "cyan"


def count_severities(tool_results: Sequence[object]) -> SeverityCounts:
    """Tally issue severities across all tool results.

    Iterates every issue on every result, normalising each to ERROR / WARNING
    / INFO via its ``get_severity()`` method. Results and issues without
    severity support are skipped gracefully.

    Args:
        tool_results: Sequence of ToolResult-like objects.

    Returns:
        SeverityCounts: Aggregated per-severity counts.
    """
    errors = 0
    warnings = 0
    info = 0
    for result in tool_results:
        issues = getattr(result, "issues", None)
        if not issues:
            continue
        for issue in issues:
            get_sev = getattr(issue, "get_severity", None)
            if not callable(get_sev):
                continue
            level = get_sev()
            if level == SeverityLevel.ERROR:
                errors += 1
            elif level == SeverityLevel.WARNING:
                warnings += 1
            elif level == SeverityLevel.INFO:
                info += 1
    return SeverityCounts(errors=errors, warnings=warnings, info=info)


def _plural(count: int, noun: str) -> str:
    """Return ``noun`` pluralised for ``count``.

    Args:
        count: Quantity the noun describes; the sign is ignored.
        noun: Singular noun. ``"info"`` is uncountable and never changes.

    Returns:
        str: The noun, with an ``s`` appended when appropriate.
    """
    if noun == "info" or abs(count) == 1:
        return noun
    return f"{noun}s"


def format_counts_line(counts: SeverityCounts) -> str:
    """Render the severity-count line printed after a check run.

    Args:
        counts: Severity tallies for the run.

    Returns:
        str: A line such as ``"Issues: 3 errors, 1 warning, 0 info"``.
    """
    return (
        f"Issues: {counts.errors} {_plural(counts.errors, 'error')}, "
        f"{counts.warnings} {_plural(counts.warnings, 'warning')}, "
        f"{counts.info} info"
    )


def counts_color(counts: SeverityCounts) -> str:
    """Return the console colour for a severity-count line.

    Args:
        counts: Severity tallies for the run.

    Returns:
        str: ``"green"`` for a clean run, ``"red"`` when any error was found,
        and ``"yellow"`` when only warnings or info issues remain.
    """
    if counts.errors:
        return COLOR_WORSE
    if counts.total:
        return "yellow"
    return COLOR_BETTER


def format_delta_line(delta: SeverityDelta) -> str:
    """Render the count-delta line printed after a check run.

    Only the severities that actually moved are listed, each with an explicit
    sign, because a run where nothing changed should not read like a list of
    zeroes.

    Args:
        delta: Change relative to the previous recorded run.

    Returns:
        str: A line such as ``"Change since last run: -12 errors, +3
        warnings"``, or ``"Change since last run: no change"``.
    """
    parts = [
        f"{value:+d} {_plural(value, noun)}"
        for value, noun in (
            (delta.errors, "error"),
            (delta.warnings, "warning"),
            (delta.info, "info"),
        )
        if value
    ]
    body = ", ".join(parts) if parts else "no change"
    return f"Change since last run: {body}"


def delta_color(delta: SeverityDelta) -> str:
    """Return the console colour for a count-delta line.

    Colour maps to the direction of *improvement*, not the arithmetic sign:
    fewer issues is better, so a drop is green and a rise is red. Severities
    are compared most-severe first, so trading an error for a warning still
    reads as an improvement without reintroducing arbitrary severity weights.

    Args:
        delta: Change relative to the previous recorded run.

    Returns:
        str: ``"green"`` when the run improved, ``"red"`` when it regressed,
        and ``"cyan"`` when nothing changed.
    """
    for value in (delta.errors, delta.warnings, delta.info):
        if value < 0:
            return COLOR_BETTER
        if value > 0:
            return COLOR_WORSE
    return COLOR_UNCHANGED
