"""Badge tables and numeric cell formatting for GitHub review comments.

Every review surface — the sticky board, the per-round review body, the error
comment — renders the same run statistics as single-row Markdown tables, so
the table primitive and the number formatting it feeds on live in one place
rather than being re-derived per surface.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lintro.ai.resolved_ai_config import format_sourced_value
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "format_badge_table",
    "format_badge_tables",
    "format_cost",
    "format_int",
    "format_tokens",
    "run_stats_primary_cells",
    "severity_counts",
]


#: Line breaks that would end a badge-table row early. ``\r\n`` is matched as
#: one break so a Windows-style value collapses to a single space, not two.
_LINE_BREAK_RE = re.compile(r"\r\n|[\r\n]")


def format_int(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def format_cost(value: float, *, estimated: bool) -> str:
    """Format a USD cost, prefixing ``~`` when the value is estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}${value:.4f}"


def format_tokens(total: int, *, estimated: bool) -> str:
    """Format a token count, prefixing ``~`` when estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}{format_int(total)} tok"


def _escape_cell(text: str) -> str:
    r"""Flatten and escape a badge-table cell so it cannot shear the row.

    A table row is one line, so a carriage return or line feed in a value ends
    the row and spills the rest of the cells into the document as prose. Line
    breaks are therefore collapsed to spaces before escaping — GFM offers no
    in-cell line break worth preserving here, and ``sanitize_comment_text``
    caps length without touching them.

    Backslashes are doubled first: escaping only the pipe would turn an input
    of ``\|`` into ``\\|``, leaving the pipe with an even number of
    preceding backslashes and readable as a delimiter again.
    """
    escaped = text.replace("\\", "\\\\").replace("|", "\\|")
    return _LINE_BREAK_RE.sub(" ", escaped)


def format_badge_table(*, cells: Sequence[tuple[str, str]]) -> list[str]:
    r"""Render one ordered row of ``(label, value)`` pairs as a badge table.

    GitHub-flavored Markdown has no chip primitive, so a single-row table —
    labels as the header, values as the one body row — is the closest thing to
    the approved chip design that renders without an external image.

    A literal ``|`` would end the cell it appears in and shear the row, so it
    is escaped here rather than at each call site — GFM honors ``\|`` inside
    code spans too, which the code-chipped values rely on. Callers still own
    their own sanitization and code-chip quoting.

    Args:
        cells: Ordered ``(label, value)`` pairs.

    Returns:
        Markdown lines, or an empty list when there is nothing to render.
    """
    if not cells:
        return []
    keys = " | ".join(_escape_cell(key) for key, _ in cells)
    dividers = " | ".join("---" for _ in cells)
    values = " | ".join(_escape_cell(value) for _, value in cells)
    return [f"| {keys} |", f"| {dividers} |", f"| {values} |"]


def format_badge_tables(
    *,
    rows: Sequence[Sequence[tuple[str, str]]],
) -> list[str]:
    """Render several badge rows as stacked single-row tables.

    Args:
        rows: Ordered row groups, each an ordered list of ``(label, value)``
            pairs. Empty groups are skipped rather than emitting a blank table.

    Returns:
        Markdown lines with one blank line between consecutive tables.
    """
    lines: list[str] = []
    for cells in rows:
        table = format_badge_table(cells=cells)
        if not table:
            continue
        if lines:
            lines.append("")
        lines.extend(table)
    return lines


def run_stats_primary_cells(*, metadata: ReviewMetadata) -> list[tuple[str, str]]:
    """Build the primary run-stats badge row shared by every review surface.

    Ordering is fixed across surfaces (epic #1905): model, est. cost, tokens
    in, tokens out. ``~`` marks values estimated locally, so a subscription run
    never presents an estimate as a billed figure.

    Args:
        metadata: Review run metadata.

    Returns:
        Ordered ``(label, value)`` pairs for the primary badge table.
    """
    estimated = metadata.token_usage_estimated
    tilde = "~" if estimated else ""
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    return [
        (
            "model",
            format_sourced_value(
                f"`{sanitize_comment_text(metadata.model, limit=60)}`",
                metadata.model_source or None,
            ),
        ),
        ("est. cost", format_cost(metadata.cost_estimate_usd, estimated=estimated)),
        ("tokens in", f"{tilde}{format_int(prompt_tokens)}"),
        ("tokens out", f"{tilde}{format_int(completion_tokens)}"),
    ]


def severity_counts(*, findings: tuple[ReviewFinding, ...]) -> dict[Severity, int]:
    """Count findings by severity.

    Questions (#1925) carry no severity semantics and are excluded, so the
    counts always match the finding set the derived verdict was computed from.

    Args:
        findings: Findings to count over.

    Returns:
        Count per severity, with every severity present.
    """
    counts: dict[Severity, int] = {Severity.P1: 0, Severity.P2: 0, Severity.P3: 0}
    for finding in findings:
        if finding.is_question:
            continue
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts
