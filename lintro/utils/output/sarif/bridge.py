"""SARIF bridge: derive SARIF inputs from ``ToolResult`` objects.

``standard_issues_from_results`` normalizes parsed lint issues and has no
dependency on the AI layer, so this module never imports :mod:`lintro.ai`.
Reconstructing the optional AI enrichment (fix suggestions and the run
summary) needs :mod:`lintro.ai.models`, so it lives in
:mod:`lintro.ai.sarif_bridge`; core receives the finished value as the
``ai_enrichment`` argument (issues #724, #1823).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.utils.findings import Finding, finding_from_issue
from lintro.utils.output.sarif.document import StandardIssue
from lintro.utils.result_reason import verdict_gap

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
        issues = result.issues
        if not issues:
            continue
        tool_name = str(result.name or "")
        for issue in issues:
            standard_issues.append(
                _to_standard_issue(issue, tool_name=tool_name),
            )
    return standard_issues


def execution_notifications(all_results: list[ToolResult]) -> list[dict[str, Any]]:
    """Build SARIF notifications for tools that gave no complete verdict.

    A tool that did not run or did not complete has no findings to report,
    and an empty ``results`` array reads as a clean scan. Each such tool is
    reported as an invocation-level ``toolExecutionNotifications`` entry
    instead of a synthetic finding (#3556). The text is the same statement
    the other report formats print (:func:`verdict_gap`).

    Args:
        all_results: The run's tool results.

    Returns:
        One notification per tool without a complete verdict: ``error`` when
        it fails the run, ``warning`` when it does not. Empty when every
        tool completed.
    """
    notifications: list[dict[str, Any]] = []
    for result in all_results:
        gap = verdict_gap(result)
        if gap is None:
            continue
        notifications.append(
            {
                "level": "warning" if result.success else "error",
                "message": {"text": " ".join(gap)},
                "properties": {"tool": result.name},
            },
        )
    return notifications


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
