"""SARIF bridge: derive SARIF inputs from ``ToolResult`` objects.

``standard_issues_from_results`` normalizes parsed lint issues and has no
dependency on the AI layer, so this module never imports :mod:`lintro.ai`.
Reconstructing the optional AI enrichment (fix suggestions and the run
summary) needs :mod:`lintro.ai.models`, so it lives in
:mod:`lintro.ai.sarif_bridge`; core receives the finished value as the
``ai_enrichment`` argument (issues #724, #1823).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.severity_level import SeverityLevel
from lintro.utils.output.sarif.document import StandardIssue

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue


def standard_issues_from_results(
    all_results: list[ToolResult],
) -> list[StandardIssue]:
    """Extract normalized standard lint issues from ToolResults.

    Reads ``result.issues`` directly (independent of AI metadata) and
    normalizes each ``BaseIssue`` into a ``StandardIssue`` carrying the
    fields required for SARIF standard-mode emission.

    Args:
        all_results: List of tool results carrying parsed lint issues.

    Returns:
        List of normalized standard issues across all results.
    """
    standard_issues: list[StandardIssue] = []
    for result in all_results:
        issues = getattr(result, "issues", None)
        if not issues:
            continue
        tool_name = str(getattr(result, "name", "") or "")
        for issue in issues:
            standard_issues.append(
                _to_standard_issue(issue, tool_name=tool_name),
            )
    return standard_issues


def _to_standard_issue(
    issue: BaseIssue,
    *,
    tool_name: str,
) -> StandardIssue:
    """Normalize a single ``BaseIssue`` into a ``StandardIssue``.

    Args:
        issue: Parsed lint issue to normalize.
        tool_name: Name of the tool that produced the issue.

    Returns:
        Normalized standard issue.
    """
    row = issue.to_display_row()
    try:
        severity = issue.get_severity()
    except (ValueError, AttributeError):
        severity = SeverityLevel.WARNING
    return StandardIssue(
        tool_name=tool_name,
        file=str(getattr(issue, "file", "") or ""),
        line=int(getattr(issue, "line", 0) or 0),
        column=int(getattr(issue, "column", 0) or 0),
        code=str(row.get("code", "") or ""),
        message=str(row.get("message", "") or ""),
        severity=severity,
        doc_url=str(
            getattr(issue, "doc_url", "") or getattr(issue, "url", "") or "",
        ),
    )
