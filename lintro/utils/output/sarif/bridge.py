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

from lintro.utils.findings import Finding, finding_from_issue
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

    Delegates the extraction to :func:`lintro.utils.findings.finding_from_issue`
    so SARIF and MCP read a ``BaseIssue`` by exactly the same rules;
    ``StandardIssue`` is the SARIF-shaped projection of that canonical
    :class:`~lintro.utils.findings.Finding` (it carries no ``fixable``, and
    names the rule identifier ``code``).

    Args:
        issue: Parsed lint issue to normalize.
        tool_name: Name of the tool that produced the issue.

    Returns:
        Normalized standard issue.
    """
    return _standard_issue_from_finding(
        finding=finding_from_issue(issue=issue, tool_name=tool_name),
    )


def _standard_issue_from_finding(*, finding: Finding) -> StandardIssue:
    """Project a canonical finding onto the SARIF-facing ``StandardIssue``.

    Args:
        finding: The canonical finding.

    Returns:
        Normalized standard issue.
    """
    return StandardIssue(
        tool_name=finding.tool,
        file=finding.file,
        line=finding.line,
        column=finding.column,
        code=finding.rule,
        message=finding.message,
        severity=finding.severity,
        doc_url=finding.doc_url,
    )
