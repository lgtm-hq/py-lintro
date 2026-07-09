"""SARIF v2.1.0 output for AI findings.

Generates SARIF (Static Analysis Results Interchange Format) output
from AI fix suggestions and summaries. Compatible with GitHub Code
Scanning, VS Code SARIF Viewer, and other SARIF-consuming tools.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lintro.ai.enums import ConfidenceLevel, RiskLevel
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.enums.severity_level import SeverityLevel

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec"
    "/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"

_CONFIDENCE_SCORE = {
    ConfidenceLevel.HIGH: 0.9,
    ConfidenceLevel.MEDIUM: 0.6,
    ConfidenceLevel.LOW: 0.3,
}

_SEVERITY_TO_SARIF_LEVEL = {
    SeverityLevel.ERROR: "error",
    SeverityLevel.WARNING: "warning",
    SeverityLevel.INFO: "note",
}


@dataclass
class StandardIssue:
    """Normalized lint issue for SARIF standard-mode emission.

    Represents a single lint finding independent of any AI enrichment,
    carrying the minimal fields required to build a SARIF result and its
    rule descriptor.

    Attributes:
        tool_name: Name of the tool that produced the issue.
        file: File path where the issue was found.
        line: Line number (1-based, 0 means unknown).
        column: Column number (1-based, 0 means unknown).
        code: Rule code/identifier for the issue.
        message: Human-readable description of the issue.
        severity: Normalized severity level for the issue.
        doc_url: Documentation URL for the rule (empty when unavailable).
    """

    tool_name: str = field(default="")
    file: str = field(default="")
    line: int = field(default=0)
    column: int = field(default=0)
    code: str = field(default="")
    message: str = field(default="")
    severity: SeverityLevel = field(default=SeverityLevel.WARNING)
    doc_url: str = field(default="")


def _severity_to_sarif_level(severity: SeverityLevel) -> str:
    """Map a normalized severity level to a SARIF result level.

    Args:
        severity: Normalized severity enum value.

    Returns:
        One of ``"error"``, ``"warning"``, or ``"note"``.
    """
    return _SEVERITY_TO_SARIF_LEVEL.get(severity, "warning")


def _risk_to_sarif_level(risk_level: RiskLevel | str) -> str:
    """Map AI risk level to SARIF result level.

    Args:
        risk_level: Risk classification (e.g. ``"behavioral-risk"``).

    Returns:
        One of ``"error"``, ``"warning"``, or ``"note"``.
    """
    normalized = str(risk_level).lower().strip() if risk_level else ""
    try:
        return RiskLevel(normalized).to_severity_label(sarif=True)
    except ValueError:
        pass
    if normalized in {"high", "critical"}:
        return "error"
    if normalized in {"medium"}:
        return "warning"
    if normalized in {"low"}:
        return "note"
    return "warning"


def _confidence_to_score(confidence: ConfidenceLevel | str) -> float:
    """Map confidence label to a numeric score.

    Args:
        confidence: Confidence level string or enum.

    Returns:
        Score between 0.0 and 1.0.
    """
    normalized = str(confidence).lower().strip() if confidence else ""
    try:
        return _CONFIDENCE_SCORE[ConfidenceLevel(normalized)]
    except (ValueError, KeyError):
        return 0.5


def _add_standard_issues(
    standard_issues: Sequence[StandardIssue],
    *,
    rules_map: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    doc_urls: dict[str, str] | None,
) -> None:
    """Append standard lint issues as SARIF rules and results in place.

    Emits one SARIF result per issue with its physical location, rule
    descriptor, severity level, and ``helpUri`` (from the issue's own
    ``doc_url`` or the ``doc_urls`` map). Runs independently of AI
    enrichment so SARIF output is populated without AI features.

    Args:
        standard_issues: Normalized lint issues to emit.
        rules_map: Shared rule descriptor map, updated in place.
        results: Shared results list, appended to in place.
        doc_urls: Optional mapping of rule codes to documentation URLs.
    """
    for issue in standard_issues:
        if issue.tool_name and issue.code:
            rule_id = f"{issue.tool_name}/{issue.code}"
        else:
            rule_id = issue.tool_name or issue.code or "unknown"

        if rule_id not in rules_map:
            rule: dict[str, Any] = {"id": rule_id}
            rule["shortDescription"] = {"text": issue.code or "lint finding"}
            help_uri = issue.doc_url
            if not help_uri and doc_urls:
                help_uri = doc_urls.get(issue.code, "") or doc_urls.get(rule_id, "")
            if help_uri:
                rule["helpUri"] = help_uri
            if issue.tool_name:
                rule["properties"] = {"tool": issue.tool_name}
            rules_map[rule_id] = rule

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": _severity_to_sarif_level(issue.severity),
            "message": {"text": issue.message or issue.code or "lint finding"},
        }

        if issue.file:
            location: dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {"uri": issue.file},
                },
            }
            if issue.line > 0:
                region: dict[str, int] = {"startLine": issue.line}
                if issue.column > 0:
                    region["startColumn"] = issue.column
                location["physicalLocation"]["region"] = region
            result["locations"] = [location]

        props: dict[str, Any] = {}
        if issue.tool_name:
            props["tool"] = issue.tool_name
        if issue.code:
            props["code"] = issue.code
        if props:
            result["properties"] = props

        results.append(result)


def to_sarif(
    suggestions: Sequence[AIFixSuggestion],
    summary: AISummary | None = None,
    *,
    tool_name: str = "lintro-ai",
    tool_version: str = "",
    doc_urls: dict[str, str] | None = None,
    standard_issues: Sequence[StandardIssue] | None = None,
) -> dict[str, Any]:
    """Convert lint and AI findings to a SARIF v2.1.0 document.

    Standard lint issues are emitted directly from ``standard_issues`` so
    SARIF output is populated even without AI features. AI fix suggestions
    are added additively on top.

    Args:
        suggestions: AI fix suggestions to include as results.
        summary: Optional AI summary to attach as run properties.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.
        doc_urls: Optional mapping of rule codes to documentation URLs.
            When provided, matching rules get a ``helpUri`` property.
        standard_issues: Normalized lint issues to emit as standard SARIF
            results, independent of any AI enrichment.

    Returns:
        SARIF document as a dictionary.
    """
    rules_map: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    if standard_issues:
        _add_standard_issues(
            standard_issues,
            rules_map=rules_map,
            results=results,
            doc_urls=doc_urls,
        )

    for s in suggestions:
        if s.tool_name and s.code:
            rule_id = f"{s.tool_name}/{s.code}"
        else:
            rule_id = s.tool_name or s.code or "unknown"

        if rule_id not in rules_map:
            rule: dict[str, Any] = {"id": rule_id}
            short_desc = s.code or "AI finding"
            rule["shortDescription"] = {"text": short_desc}
            if s.explanation:
                rule["fullDescription"] = {"text": s.explanation}
            # Attach documentation URL when available
            if doc_urls:
                help_uri = doc_urls.get(s.code, "") or doc_urls.get(rule_id, "")
                if help_uri:
                    rule["helpUri"] = help_uri
            if s.tool_name:
                rule["properties"] = {"tool": s.tool_name}
            rules_map[rule_id] = rule

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": _risk_to_sarif_level(s.risk_level),
            "message": {"text": s.explanation or "AI fix available"},
        }

        # Location
        if s.file:
            location: dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {"uri": s.file},
                },
            }
            if s.line > 0:
                location["physicalLocation"]["region"] = {
                    "startLine": s.line,
                }
            result["locations"] = [location]

        # Fix suggestion — only emit when there is a real replacement target
        if s.suggested_code and s.file and s.line > 0:
            deleted_region: dict[str, int] = {"startLine": s.line}
            if s.original_code:
                trimmed = s.original_code.rstrip("\n")
                line_count = max(1, trimmed.count("\n") + 1)
                deleted_region["endLine"] = s.line + line_count - 1
            fix: dict[str, Any] = {
                "description": {"text": s.explanation or "AI suggestion"},
                "artifactChanges": [
                    {
                        "artifactLocation": {"uri": s.file},
                        "replacements": [
                            {
                                "deletedRegion": deleted_region,
                                "insertedContent": {
                                    "text": s.suggested_code,
                                },
                            },
                        ],
                    },
                ],
            }
            result["fixes"] = [fix]

        # Properties
        props: dict[str, Any] = {}
        if s.confidence:
            props["confidence"] = s.confidence
            props["confidenceScore"] = _confidence_to_score(s.confidence)
        if s.risk_level:
            props["riskLevel"] = s.risk_level
        if s.tool_name:
            props["tool"] = s.tool_name
        if s.cost_estimate:
            props["costEstimate"] = s.cost_estimate
        if props:
            result["properties"] = props

        results.append(result)

    # Build driver
    driver: dict[str, Any] = {"name": tool_name}
    if tool_version:
        driver["version"] = tool_version
    if rules_map:
        driver["rules"] = list(rules_map.values())

    run: dict[str, Any] = {
        "tool": {"driver": driver},
        "results": results,
    }

    # Attach summary as run properties when any field is populated
    if summary and (
        summary.overview
        or summary.key_patterns
        or summary.priority_actions
        or summary.triage_suggestions
        or summary.estimated_effort
    ):
        run["properties"] = {
            "aiSummary": {
                "overview": summary.overview,
                "keyPatterns": summary.key_patterns,
                "priorityActions": summary.priority_actions,
                "triageSuggestions": summary.triage_suggestions,
                "estimatedEffort": summary.estimated_effort,
            },
        }

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [run],
    }


def render_fixes_sarif(
    suggestions: Sequence[AIFixSuggestion],
    summary: AISummary | None = None,
    *,
    tool_name: str = "lintro-ai",
    tool_version: str = "",
    doc_urls: dict[str, str] | None = None,
    standard_issues: Sequence[StandardIssue] | None = None,
) -> str:
    """Render lint and AI findings as a SARIF JSON string.

    Args:
        suggestions: AI fix suggestions.
        summary: Optional AI summary.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.
        doc_urls: Optional mapping of rule codes to documentation URLs.
        standard_issues: Normalized lint issues to emit as standard results.

    Returns:
        Pretty-printed SARIF JSON string.
    """
    sarif = to_sarif(
        suggestions,
        summary,
        tool_name=tool_name,
        tool_version=tool_version,
        doc_urls=doc_urls,
        standard_issues=standard_issues,
    )
    return json.dumps(sarif, indent=2)


def write_sarif(
    suggestions: Sequence[AIFixSuggestion],
    summary: AISummary | None = None,
    *,
    output_path: Path,
    tool_name: str = "lintro-ai",
    tool_version: str = "",
    doc_urls: dict[str, str] | None = None,
    standard_issues: Sequence[StandardIssue] | None = None,
) -> None:
    """Write lint and AI findings as a SARIF file.

    Args:
        suggestions: AI fix suggestions.
        summary: Optional AI summary.
        output_path: Path to write the SARIF file.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.
        doc_urls: Optional mapping of rule codes to documentation URLs.
        standard_issues: Normalized lint issues to emit as standard results.
    """
    sarif = to_sarif(
        suggestions,
        summary,
        tool_name=tool_name,
        tool_version=tool_version,
        doc_urls=doc_urls,
        standard_issues=standard_issues,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sarif, indent=2) + "\n",
        encoding="utf-8",
    )
