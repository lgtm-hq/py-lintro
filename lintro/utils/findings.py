"""Canonical structured shape for a lint finding and a per-tool summary.

Before this module every machine-readable surface extracted its own subset of
:class:`~lintro.parsers.base_issue.BaseIssue`: ``--output-format json`` emitted
``{file, line, code, message, doc_url}``, the SARIF bridge built its own
``StandardIssue`` with ``column`` and ``severity``, and CSV had a third shape.
Adding MCP would have made a fourth. :class:`Finding` is the superset all of
them can be projected from, so a new consumer normalizes a ``BaseIssue`` in
exactly one place.

The JSON document's own key set is deliberately *not* changed here — it is a
published output contract. What changed is where its data comes from: SARIF
and MCP both build on :func:`findings_from_results`, and JSON's narrower body
is a projection of the same extraction rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action
from lintro.enums.severity_level import SeverityLevel
from lintro.enums.tool_run_status import ToolRunStatus, tool_run_status
from lintro.formatters.formatter import merge_detected_and_remaining

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue

__all__ = [
    "Finding",
    "FindingScope",
    "ToolRunSummary",
    "finding_from_issue",
    "findings_from_result",
    "findings_from_results",
    "issues_for_result",
    "tool_summaries_from_results",
    "tool_summary_from_result",
]


class FindingScope(StrEnum):
    """Which of a fix run's issues a consumer wants to see.

    A fix run knows two populations: everything the tool detected before it
    started, and what is still unfixed now. Neither is universally right —
    ``--output-format json`` reports the union so a user can see what was done,
    while an agent asking a formatter to run wants the residue it must handle
    itself. A check run has only one population and ignores this entirely.

    Attributes:
        ALL: Pre-fix detections merged with the post-fix remainder.
        REMAINING: Only the issues the fix run could not resolve.
    """

    ALL = auto()
    REMAINING = auto()


@dataclass(frozen=True)
class Finding:
    """One normalized lint finding, independent of any output format.

    Attributes:
        tool: Name of the tool that produced the finding.
        file: Path to the file the finding refers to, as the tool reported it.
        line: 1-based line number, or 0 when the tool reported none.
        column: 1-based column number, or 0 when the tool reported none.
        rule: Tool-specific rule identifier (ruff's ``F401``, hadolint's
            ``DL3008``). Empty when the tool emits unclassified diagnostics.
        severity: Normalized severity.
        message: Human-readable description of the finding.
        fixable: Whether the producing tool can fix this finding itself.
        doc_url: Documentation URL for ``rule``, or an empty string.
    """

    tool: str
    file: str
    line: int
    column: int
    rule: str
    severity: SeverityLevel
    message: str
    fixable: bool
    doc_url: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the finding to a JSON-compatible dict.

        ``doc_url`` is omitted when empty, matching the JSON document's
        convention of not emitting a key that carries no information.

        Returns:
            dict[str, Any]: The finding's wire representation.
        """
        data: dict[str, Any] = {
            "tool": self.tool,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "rule": self.rule,
            "severity": str(self.severity),
            "message": self.message,
            "fixable": self.fixable,
        }
        if self.doc_url:
            data["doc_url"] = self.doc_url
        return data


@dataclass(frozen=True)
class ToolRunSummary:
    """Per-tool outcome of a run.

    Attributes:
        tool: Name of the tool.
        status: Collapsed outcome.
        issue_count: Number of findings attributed to this tool.
        duration: Wall-clock seconds the tool took, or ``None`` when the run
            did not record one (a tool that never executed, or a result
            constructed outside the executor).
        fixed_count: How many issues the tool fixed, or ``None`` for a check
            run and for fix-incapable tools.
        skip_reason: Why the tool was skipped, or ``None``.
    """

    tool: str
    status: ToolRunStatus
    issue_count: int
    duration: float | None
    fixed_count: int | None = None
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the summary to a JSON-compatible dict.

        Returns:
            dict[str, Any]: The summary's wire representation.
        """
        data: dict[str, Any] = {
            "tool": self.tool,
            "status": str(self.status),
            "issue_count": self.issue_count,
            "duration": self.duration,
        }
        if self.fixed_count is not None:
            data["fixed_count"] = self.fixed_count
        if self.skip_reason:
            data["skip_reason"] = self.skip_reason
        return data


def _severity_of(*, issue: BaseIssue) -> SeverityLevel:
    """Resolve an issue's severity, falling back to a warning.

    Args:
        issue: The parsed issue.

    Returns:
        SeverityLevel: The normalized severity.
    """
    try:
        return issue.get_severity()
    except (ValueError, AttributeError):
        return SeverityLevel.WARNING


def finding_from_issue(*, issue: BaseIssue, tool_name: str) -> Finding:
    """Normalize a single parsed issue into a :class:`Finding`.

    ``rule`` and ``message`` come from ``to_display_row`` because subclasses
    remap those onto their own attribute names (``code`` may live on ``rule``,
    ``check``, ``id``, …) and the display row is the one place that mapping is
    already resolved.

    Args:
        issue: The parsed issue.
        tool_name: Name of the tool that produced it.

    Returns:
        Finding: The normalized finding.
    """
    row = issue.to_display_row()
    fixable_attr = issue.DISPLAY_FIELD_MAP.get("fixable", "fixable")
    return Finding(
        tool=tool_name,
        file=str(getattr(issue, "file", "") or ""),
        line=int(getattr(issue, "line", 0) or 0),
        column=int(getattr(issue, "column", 0) or 0),
        rule=str(row.get("code", "") or ""),
        severity=_severity_of(issue=issue),
        message=str(row.get("message", "") or ""),
        fixable=bool(getattr(issue, fixable_attr, False)),
        doc_url=str(getattr(issue, "doc_url", "") or getattr(issue, "url", "") or ""),
    )


def issues_for_result(
    *,
    result: ToolResult,
    action: Action,
    scope: FindingScope = FindingScope.ALL,
) -> list[BaseIssue]:
    """Return the issues a consumer should report for one tool result.

    A check run has one population and returns ``result.issues`` verbatim. A
    fix run is filtered by ``scope``; see :class:`FindingScope`. The remaining
    issues are read off the tail of ``result.issues``, which is the ordering
    contract :class:`~lintro.models.core.tool_result.ToolResult` documents.

    Args:
        result: The tool result.
        action: The action the run performed.
        scope: Which population of a fix run's issues to return.

    Returns:
        list[BaseIssue]: Issues attributed to this result.
    """
    issues = list(result.issues or [])
    if action != Action.FIX:
        return issues
    if scope == FindingScope.ALL:
        return merge_detected_and_remaining(result.initial_issues, result.issues)
    remaining = result.remaining_issues_count
    if remaining is None:
        return issues
    return issues[len(issues) - remaining :] if remaining else []


def findings_from_result(
    *,
    result: ToolResult,
    action: Action,
    scope: FindingScope = FindingScope.ALL,
) -> list[Finding]:
    """Normalize one tool result's issues into findings.

    Args:
        result: The tool result.
        action: The action the run performed.
        scope: Which population of a fix run's issues to report.

    Returns:
        list[Finding]: Normalized findings for this tool.
    """
    tool_name = str(result.name or "")
    return [
        finding_from_issue(issue=issue, tool_name=tool_name)
        for issue in issues_for_result(result=result, action=action, scope=scope)
    ]


def findings_from_results(
    *,
    results: Sequence[ToolResult],
    action: Action,
    scope: FindingScope = FindingScope.ALL,
) -> list[Finding]:
    """Normalize every tool result's issues into one flat findings list.

    Args:
        results: Tool results from a run.
        action: The action the run performed.
        scope: Which population of a fix run's issues to report.

    Returns:
        list[Finding]: Normalized findings across all tools, in tool order.
    """
    findings: list[Finding] = []
    for result in results:
        findings.extend(
            findings_from_result(result=result, action=action, scope=scope),
        )
    return findings


def tool_summary_from_result(
    *,
    result: ToolResult,
    action: Action,
    scope: FindingScope = FindingScope.ALL,
) -> ToolRunSummary:
    """Summarize one tool's outcome.

    Args:
        result: The tool result.
        action: The action the run performed.
        scope: Which population of a fix run's issues ``issue_count`` counts.

    Returns:
        ToolRunSummary: The per-tool summary.
    """
    issue_count = len(issues_for_result(result=result, action=action, scope=scope))
    return ToolRunSummary(
        tool=str(result.name or ""),
        status=tool_run_status(result=result, issue_count=issue_count),
        issue_count=issue_count,
        duration=result.duration_seconds,
        fixed_count=result.fixed_issues_count if action == Action.FIX else None,
        skip_reason=result.skip_reason,
    )


def tool_summaries_from_results(
    *,
    results: Sequence[ToolResult],
    action: Action,
    scope: FindingScope = FindingScope.ALL,
) -> list[ToolRunSummary]:
    """Summarize every tool's outcome, in run order.

    Args:
        results: Tool results from a run.
        action: The action the run performed.
        scope: Which population of a fix run's issues ``issue_count`` counts.

    Returns:
        list[ToolRunSummary]: One summary per tool result.
    """
    return [
        tool_summary_from_result(result=result, action=action, scope=scope)
        for result in results
    ]
