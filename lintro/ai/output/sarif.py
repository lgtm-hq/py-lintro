"""SARIF v2.1.0 output for AI findings.

Generates SARIF (Static Analysis Results Interchange Format) output
from AI fix suggestions and summaries. Compatible with GitHub Code
Scanning, VS Code SARIF Viewer, and other SARIF-consuming tools.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lintro.ai.models import AIFixSuggestion, AISummary

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec"
    "/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"


def _risk_to_sarif_level(risk_level: str) -> str:
    """Map AI risk level to SARIF result level.

    Args:
        risk_level: Risk classification (e.g. ``"high"``, ``"low"``).

    Returns:
        One of ``"error"``, ``"warning"``, or ``"note"``.
    """
    normalized = risk_level.lower().strip() if risk_level else ""
    if normalized in {"high", "critical"}:
        return "error"
    if normalized in {"medium", "behavioral-risk"}:
        return "warning"
    if normalized in {"low", "safe-style"}:
        return "note"
    return "warning"


def _confidence_to_score(confidence: str) -> float:
    """Map confidence label to a numeric score.

    Args:
        confidence: Confidence level string.

    Returns:
        Score between 0.0 and 1.0.
    """
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return mapping.get(confidence.lower().strip() if confidence else "", 0.5)


def to_sarif(
    suggestions: Sequence[AIFixSuggestion],
    summary: AISummary | None = None,
    *,
    tool_name: str = "lintro-ai",
    tool_version: str = "",
) -> dict[str, Any]:
    """Convert AI findings to a SARIF v2.1.0 document.

    Args:
        suggestions: AI fix suggestions to include as results.
        summary: Optional AI summary to attach as run properties.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.

    Returns:
        SARIF document as a dictionary.
    """
    rules_map: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for s in suggestions:
        rule_id = s.code or "unknown"

        if rule_id not in rules_map:
            rule: dict[str, Any] = {"id": rule_id}
            short_desc = s.code or "AI finding"
            rule["shortDescription"] = {"text": short_desc}
            if s.explanation:
                rule["fullDescription"] = {"text": s.explanation}
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
            if s.line:
                location["physicalLocation"]["region"] = {
                    "startLine": s.line,
                }
            result["locations"] = [location]

        # Fix suggestion
        if s.suggested_code and s.file:
            fix: dict[str, Any] = {
                "description": {"text": s.explanation or "AI suggestion"},
                "artifactChanges": [
                    {
                        "artifactLocation": {"uri": s.file},
                        "replacements": [
                            {
                                "deletedRegion": {
                                    "startLine": s.line or 1,
                                },
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

    # Attach summary as run properties
    if summary and summary.overview:
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
) -> str:
    """Render AI findings as a SARIF JSON string.

    Args:
        suggestions: AI fix suggestions.
        summary: Optional AI summary.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.

    Returns:
        Pretty-printed SARIF JSON string.
    """
    sarif = to_sarif(
        suggestions,
        summary,
        tool_name=tool_name,
        tool_version=tool_version,
    )
    return json.dumps(sarif, indent=2)


def write_sarif(
    suggestions: Sequence[AIFixSuggestion],
    summary: AISummary | None = None,
    *,
    output_path: Path,
    tool_name: str = "lintro-ai",
    tool_version: str = "",
) -> None:
    """Write AI findings as a SARIF file.

    Args:
        suggestions: AI fix suggestions.
        summary: Optional AI summary.
        output_path: Path to write the SARIF file.
        tool_name: Name for the SARIF tool driver.
        tool_version: Version string for the tool driver.
    """
    sarif = to_sarif(
        suggestions,
        summary,
        tool_name=tool_name,
        tool_version=tool_version,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sarif, indent=2) + "\n")
