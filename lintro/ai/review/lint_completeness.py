"""Tell lint results that answered apart from ones that did not (#3497).

The review prompt's lint digest must never read a tool's silence as a clean
result when the tool did not run or did not complete, and such a tool is not
counted among the tools that ran. A saved ``--lint-report`` is rebuilt by
:mod:`lintro.ai.review.lint_report_entry` so it is counted the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.models.core.tool_result import ToolResult
from lintro.utils.result_reason import incomplete_reason

__all__ = ["LintRunCounts", "count_lint_results"]


@dataclass(frozen=True)
class LintRunCounts:
    """How many lint tools answered, how many did not, and what they found.

    Attributes:
        ran: Tools that produced a complete verdict. A skipped or unavailable
            tool is counted in neither field: it made no claim either way.
        incomplete: Tools that did not run or did not complete.
        issues: Findings reported by all tools, incomplete ones included.
    """

    ran: int
    incomplete: int
    issues: int


def count_lint_results(results: list[ToolResult]) -> LintRunCounts:
    """Count the tools that ran apart from the ones that did not complete.

    Args:
        results: Tool results from a lint run or a saved report.

    Returns:
        The counts, so neither a refused nor a skipped tool is counted as
        one that ran.
    """
    incomplete = sum(1 for result in results if incomplete_reason(result))
    no_claim = sum(1 for result in results if result.skipped or result.unavailable)
    return LintRunCounts(
        ran=len(results) - incomplete - no_claim,
        incomplete=incomplete,
        issues=sum(result.issues_count or 0 for result in results),
    )
