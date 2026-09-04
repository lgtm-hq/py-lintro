"""Parsing of raw model finding payloads into :class:`ReviewFinding` objects.

Extracted from :mod:`lintro.ai.review.orchestrator` so that the custom review
agent pass (issue #1245) can reuse the exact same finding parsing and severity
normalization as the built-in checklist passes instead of forking them.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.models.finding_occurrence import parse_occurrences
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.suggested_change import parse_suggested_change
from lintro.ai.review.narrative_parser import collapse_to_single_line
from lintro.ai.review.severity_gate import apply_p1_evidence_gate

__all__ = [
    "SEVERITY_SYNONYMS",
    "normalize_evidence_style",
    "normalize_finding_kind",
    "normalize_severity",
    "parse_findings",
    "parse_flagged_files",
    "parse_severity_label",
    "reject_context_findings",
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


def normalize_finding_kind(*, raw: object) -> FindingKind:
    """Normalize a model-reported ``kind`` value to a member.

    Args:
        raw: Raw ``kind`` value from a parsed model response.

    Returns:
        The matching member, or :data:`FindingKind.FINDING` when the value is
        absent or unrecognized. Defaulting to ``FINDING`` is the conservative
        choice: a mislabelled question still gets reviewed and rendered, while
        a mislabelled finding would silently vanish from the verdict.
    """
    try:
        return FindingKind(str(raw).strip().lower())
    except ValueError:
        return FindingKind.FINDING


def normalize_evidence_style(*, raw: object) -> EvidenceStyle:
    """Normalize a model-reported ``evidence_style`` value to a member.

    Args:
        raw: Raw ``evidence_style`` value from a parsed model response.

    Returns:
        The matching member, or :data:`EvidenceStyle.DIFF_LOCAL` when the
        value is absent or unrecognized. The field drives display only, so an
        unknown label falls back to the unchipped default rather than
        asserting an evidence basis the model did not claim.
    """
    return EvidenceStyle.coerce(raw)


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
            wins over whatever the model labels an individual finding — it
            also exempts the pass from the P1 evidence gate.

    Returns:
        Parsed findings in payload order. Non-mapping entries are dropped.
        Every field added by #1925 (``kind``, ``failure_scenario``,
        ``evidence_style``, ``occurrences``) and by #1911
        (``suggested_change``) is optional and degrades to its default.
        Unless ``severity_override`` is set, the P1 evidence gate runs: a P1
        without a concrete failure scenario comes back as a marked P2.
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
        # str() would turn a null failure_scenario into the truthy literal
        # "None" and walk an unevidenced P1 straight through the gate.
        failure_scenario = item.get("failure_scenario", "")
        if not isinstance(failure_scenario, str):
            failure_scenario = ""
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
                kind=normalize_finding_kind(raw=item.get("kind", "")),
                failure_scenario=failure_scenario,
                evidence_style=normalize_evidence_style(
                    raw=item.get("evidence_style", ""),
                ),
                occurrences=parse_occurrences(item.get("occurrences")),
                suggested_change=parse_suggested_change(
                    item.get("suggested_change"),
                ),
            ),
        )
    if severity_override is not None:
        # An author-declared severity policy is configuration, not model
        # output, so the calibration gate has nothing to correct: downgrading
        # it would silently override the agent's own front matter.
        return tuple(findings)
    return apply_p1_evidence_gate(findings=findings)


def parse_flagged_files(*, raw_flags: object) -> tuple[FlaggedFile, ...]:
    """Parse reviewer re-read requests from untrusted model JSON.

    Args:
        raw_flags: Raw ``flagged_files`` value from a parsed model response.

    Returns:
        Flags with a non-empty path and reason. Guards (allowlist, one-way,
        cap) are applied later by the coverage classifier.
    """
    if not isinstance(raw_flags, list):
        return ()
    flags: list[FlaggedFile] = []
    for item in raw_flags:
        if not isinstance(item, dict):
            continue
        parsed = FlaggedFile.from_dict(item)
        if parsed is not None:
            flags.append(parsed)
    return tuple(flags)


def reject_context_findings(
    *,
    findings: tuple[ReviewFinding, ...],
    allowed_paths: set[str],
    eligible_paths: set[str],
) -> tuple[tuple[ReviewFinding, ...], tuple[FlaggedFile, ...]]:
    """Drop findings on read-only context files; convert others to flags.

    Findings against files that needed review are kept. Findings against
    other review-eligible paths become guarded flags. Everything else is
    discarded so a prompt instruction is not the only boundary.

    Args:
        findings: Parsed findings from one chunk or the merged run.
        allowed_paths: Paths this pass may emit findings against (the
            resume queue, typically intersected with the chunk).
        eligible_paths: Review-eligible paths in the current diff.

    Returns:
        Kept findings and flags converted from out-of-scope findings.
    """
    kept: list[ReviewFinding] = []
    flags: list[FlaggedFile] = []
    allowed = {_normalize_finding_path(path) for path in allowed_paths}
    eligible = {_normalize_finding_path(path) for path in eligible_paths}
    for finding in findings:
        path = _normalize_finding_path(finding.file)
        if path in allowed:
            kept.append(finding)
            continue
        if path in eligible:
            reason = finding.title.strip() or "re-read requested"
            flags.append(FlaggedFile(path=path, reason=reason))
            logger.info(
                "Converted context-file finding on {path} to a flagged re-read",
                path=path,
            )
            continue
        logger.info(
            "Rejected finding on non-review path {path}",
            path=path or "(empty)",
        )
    return tuple(kept), tuple(flags)


def _normalize_finding_path(path: str) -> str:
    """Normalize a model-reported path for allowlist comparison."""
    return path.strip().replace("\\", "/").removeprefix("./")
