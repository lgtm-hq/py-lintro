"""Parsing of raw model finding payloads into :class:`ReviewFinding` objects.

Extracted from :mod:`lintro.ai.review.orchestrator` so that the custom review
agent pass (issue #1245) can reuse the exact same finding parsing and severity
normalization as the built-in checklist passes instead of forking them.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.narrative_parser import collapse_to_single_line

__all__ = [
    "SEVERITY_SYNONYMS",
    "normalize_severity",
    "parse_findings",
    "parse_severity_label",
]

SEVERITY_SYNONYMS: dict[str, Severity] = {
    "CRITICAL": Severity.P1,
    "BLOCKER": Severity.P1,
    "BLOCKING": Severity.P1,
    "HIGH": Severity.P1,
    "SEVERE": Severity.P1,
    "ERROR": Severity.P1,
    "MAJOR": Severity.P2,
    "MEDIUM": Severity.P2,
    "MODERATE": Severity.P2,
    "WARNING": Severity.P2,
    "WARN": Severity.P2,
    "MINOR": Severity.P3,
    "LOW": Severity.P3,
    "TRIVIAL": Severity.P3,
    "INFO": Severity.P3,
    "NOTE": Severity.P3,
}


def parse_severity_label(*, raw: object) -> Severity | None:
    """Strictly resolve a severity label without a fail-closed fallback.

    Used for author-controlled input (such as custom review agent front
    matter) where an unrecognized label must surface as a configuration
    error rather than silently becoming a blocking severity.

    Args:
        raw: Raw severity label to resolve.

    Returns:
        The matching ``Severity`` member, or ``None`` when the label is
        neither a canonical ``P*`` value nor a known synonym.
    """
    normalized = str(raw).strip().upper()
    try:
        return Severity(normalized)
    except ValueError:
        return SEVERITY_SYNONYMS.get(normalized)


def normalize_severity(*, raw: object) -> Severity:
    """Normalize an unvalidated model severity value to a ``Severity`` member.

    Strips surrounding whitespace and uppercases the value before matching
    against the canonical ``P1``/``P2``/``P3`` labels. When the value is not a
    canonical label, it is matched against a table of common synonyms so that
    blocking words like ``critical`` map to ``P1`` rather than being downgraded.
    A truly unknown value fails closed to ``Severity.P1`` and is logged: since
    the exit gate only trips on ``P1``, an unrecognized label (e.g. ``fatal``)
    must be treated as blocking so malformed model output can never silently
    pass the gate. The synonym table already captures the benign labels, so the
    fallback is deliberately conservative.

    Synonym mapping:
        P1: critical, blocker, blocking, high, severe, error.
        P2: major, medium, moderate, warning, warn.
        P3: minor, low, trivial, info, note.

    Args:
        raw: Raw severity value from a parsed model response.

    Returns:
        The matching ``Severity`` member, a synonym-mapped member, or
        ``Severity.P1`` (fail-closed) when the value is unrecognized.
    """
    resolved = parse_severity_label(raw=raw)
    if resolved is not None:
        return resolved

    logger.warning(
        "Unknown finding severity {raw!r}; failing closed to P1.",
        raw=raw,
    )
    return Severity.P1


def parse_findings(
    *,
    raw_findings: object,
    source: str = "",
    severity_override: Severity | None = None,
) -> tuple[ReviewFinding, ...]:
    """Parse findings from AI JSON.

    Args:
        raw_findings: Raw ``findings`` value from a parsed model response.
        source: Provenance label recorded on every parsed finding. Empty for
            the built-in checklist passes; the agent name for custom review
            agent passes.
        severity_override: When set, every parsed finding is assigned this
            severity instead of the model-reported one. Custom review agents
            declare a severity policy in front matter, and that declared policy
            wins over whatever the model labels an individual finding.

    Returns:
        Parsed findings in payload order. Non-mapping entries are dropped.
    """
    if not isinstance(raw_findings, list):
        return ()
    findings: list[ReviewFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        line = item.get("line", 0)
        if not isinstance(line, int):
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0
        checklist_ids_raw = item.get("checklist_ids", [])
        checklist_ids: tuple[int, ...] = ()
        if isinstance(checklist_ids_raw, list):
            checklist_ids = tuple(
                checklist_id
                for checklist_id in checklist_ids_raw
                if isinstance(checklist_id, int)
            )
        severity = (
            severity_override
            if severity_override is not None
            else normalize_severity(raw=item.get("severity", "P3"))
        )
        findings.append(
            ReviewFinding(
                severity=severity,
                category=str(item.get("category", "logic-bug")),
                file=str(item.get("file", "")),
                line=line,
                title=collapse_to_single_line(text=str(item.get("title", ""))),
                description=str(item.get("description", "")),
                cause=str(item.get("cause", "")),
                fix=str(item.get("fix", "")),
                confidence=str(item.get("confidence", "medium")),
                checklist_ids=checklist_ids,
                suggested_code=str(item.get("suggested_code", "")),
                source=source,
            ),
        )
    return tuple(findings)
