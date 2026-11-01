"""Outcome status for a single tool within a run.

``ToolResult`` records the outcome of a tool as three independent booleans
(``success``, ``skipped``, ``timed_out``) plus an issue count. Every consumer
that wants to *name* that outcome has so far re-derived it inline, most
visibly the human summary tables. This enum is the single collapsed
vocabulary, so the machine-readable surfaces (MCP tool summaries today) all
agree on what "the tool passed" means.
"""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol


class ToolOutcome(Protocol):
    """Structural view of the result fields this module reads.

    ``lintro.enums`` is the bottom layer, so it must not name
    ``lintro.models.core.ToolResult`` even under ``TYPE_CHECKING``: an
    ``import-linter`` layering contract counts type-checking imports as edges.
    Read-only properties let frozen ``ToolResult`` instances and mutable
    plugin attributes satisfy this protocol structurally.
    """

    @property
    def success(self) -> bool:
        """Read the tool's verdict.

        Returns:
            Whether the tool reported a clean run.
        """
        ...

    @property
    def skipped(self) -> bool:
        """Read whether execution was skipped.

        Returns:
            Whether the tool never executed.
        """
        ...

    @property
    def timed_out(self) -> bool:
        """Read whether execution exceeded its deadline.

        Returns:
            Whether the tool's subprocess timed out.
        """
        ...


class ToolRunStatus(StrEnum):
    """Collapsed outcome of one tool's participation in a run.

    Attributes:
        PASSED: The tool ran and reported no issues.
        ISSUES: The tool ran and reported at least one issue.
        SKIPPED: The tool did not run (unavailable, disabled, filtered out).
        UNAVAILABLE: A capability probe could not establish a runnable tool.
        TIMED_OUT: The tool's subprocess exceeded its deadline and was killed.
        ERRORED: The tool failed for a reason that is not a lint finding.
    """

    PASSED = auto()
    ISSUES = auto()
    SKIPPED = auto()
    UNAVAILABLE = auto()
    TIMED_OUT = auto()
    ERRORED = auto()


def tool_run_status(*, result: ToolOutcome, issue_count: int) -> ToolRunStatus:
    """Derive the collapsed status of a completed tool result.

    Precedence is deliberate: a skipped tool never ran, a timeout is an
    execution failure rather than a finding, and a tool that reports issues is
    described by those issues even though it also sets ``success=False``.

    Args:
        result: The completed tool result.
        issue_count: Issue count as the caller counted it, which may differ
            from ``result.issues_count`` when detected and remaining issues
            were merged for a fix run.

    Returns:
        ToolRunStatus: The collapsed status.
    """
    if getattr(result, "unavailable", False) is True:
        return ToolRunStatus.UNAVAILABLE
    if result.skipped:
        return ToolRunStatus.SKIPPED
    if result.timed_out:
        return ToolRunStatus.TIMED_OUT
    if issue_count > 0:
        return ToolRunStatus.ISSUES
    if not result.success:
        return ToolRunStatus.ERRORED
    return ToolRunStatus.PASSED
