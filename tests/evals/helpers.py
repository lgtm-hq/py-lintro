"""Synthetic fixtures shared by the eval harness tests."""

from __future__ import annotations

import json
from typing import Any

from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.models.review_finding import ReviewFinding, Severity


def make_finding(
    *,
    title: str,
    file: str = "lintro/example.py",
    category: str = "correctness",
    severity: Severity = Severity.P2,
    line: int = 10,
    kind: FindingKind = FindingKind.FINDING,
) -> ReviewFinding:
    """Build a synthetic finding for metric tests.

    Args:
        title: Finding title, which drives its fingerprint.
        file: Repository-relative path.
        category: Finding category label.
        severity: Finding severity.
        line: Line number; never part of a fingerprint.
        kind: Whether the entry is a finding or a question.

    Returns:
        A finding carrying only the fields the metrics read.
    """
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        description="",
        cause="",
        fix="",
        confidence="high",
        kind=kind,
    )


def make_payload(
    *,
    titles: tuple[str, ...],
    severity: str = "P2",
    cost_usd: float = 0.25,
) -> str:
    """Build a ``lintro review --output json`` payload as text.

    Args:
        titles: Titles of the findings the payload reports.
        severity: Severity label applied to every finding.
        cost_usd: Value placed at ``metadata.cost_estimate_usd``.

    Returns:
        JSON text shaped like the review command's own output.
    """
    payload: dict[str, Any] = {
        "metadata": {"cost_estimate_usd": cost_usd},
        "readiness_verdict": "blocked",
        "findings": [
            {
                "severity": severity,
                "category": "correctness",
                "file": "lintro/example.py",
                "line": 10 + index,
                "title": title,
                "description": "",
                "confidence": "high",
                "kind": "finding",
            }
            for index, title in enumerate(titles)
        ],
    }
    return json.dumps(payload)
