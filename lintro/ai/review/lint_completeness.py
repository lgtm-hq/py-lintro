"""Tell lint results that answered apart from ones that did not (#3497).

The review prompt's lint digest must never read a tool's silence as a clean
result when the tool did not run or did not complete, and such a tool is not
counted among the tools that ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lintro.models.core.result_completeness import is_incomplete_result
from lintro.models.core.tool_result import ToolResult
from lintro.utils.quoting import quote_first_line

__all__ = ["LintRunCounts", "count_lint_results", "incomplete_lint_reason"]

#: Longest reason quoted for a tool that did not run or did not complete.
_INCOMPLETE_REASON_LIMIT: Final[int] = 160


@dataclass(frozen=True)
class LintRunCounts:
    """How many lint tools answered, how many did not, and what they found.

    Attributes:
        ran: Tools that produced a complete verdict.
        incomplete: Tools that did not run or did not complete.
        issues: Findings reported by all tools, incomplete ones included.
    """

    ran: int
    incomplete: int
    issues: int


def incomplete_lint_reason(result: ToolResult) -> str | None:
    """Name why a lint result is no complete verdict, or None when it is one.

    A result is incomplete when it failed to execute (a refused command, an
    npx integrity refusal, a crash), timed out, left its residual unknown,
    printed a report that could not be fully parsed, or failed without a
    single finding to explain the failure. ``findings_complete`` alone is not
    used: legacy plugins never set it, so it would flag every one of them.

    Args:
        result: One tool's lint result.

    Returns:
        A one-line reason, cleaned of terminal control sequences, or None.
    """
    failed_silently = not result.success and not result.skipped and not result.issues
    if not (is_incomplete_result(result) or failed_silently):
        return None
    if result.timed_out:
        return "timed out"
    return quote_first_line(result.output, limit=_INCOMPLETE_REASON_LIMIT) or (
        "execution failed" if result.execution_failed else "failed with no findings"
    )


def count_lint_results(results: list[ToolResult]) -> LintRunCounts:
    """Count the tools that ran apart from the ones that did not complete.

    Args:
        results: Tool results from a lint run or a saved report.

    Returns:
        The counts, so a refused tool is never counted as one that ran.
    """
    incomplete = sum(1 for result in results if incomplete_lint_reason(result))
    return LintRunCounts(
        ran=len(results) - incomplete,
        incomplete=incomplete,
        issues=sum(result.issues_count or 0 for result in results),
    )
