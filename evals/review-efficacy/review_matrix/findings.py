"""Bridge between review payloads and the production finding matcher.

Nothing here reimplements matching. Identity, ordinal assignment, verdicts and
match outcomes all come from :mod:`lintro.ai.review.finding_matcher`; this
module only adapts the harness's inputs to the shapes that module expects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    derive_verdict,
    fingerprint_for,
    match_findings,
)
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "MATCH_ROUND",
    "fingerprints_for",
    "findings_from_payload",
    "match_against",
    "matched_count",
    "records_for",
    "verdict_for",
]

#: Round number used for the "candidate" side of every harness comparison.
#: The baseline side is always round 1, so a matched pair reads as *carried*.
MATCH_ROUND = 2


def findings_from_payload(payload: Mapping[str, Any]) -> tuple[ReviewFinding, ...]:
    """Parse the ``findings`` block of a ``lintro review --output json`` payload.

    Only the fields that carry finding identity, severity and question-ness are
    read back: those are what the matcher and the derived verdict consume, and
    a harness that reconstructed the prose fields would silently depend on
    review-copy formatting.

    Args:
        payload: Decoded review payload.

    Returns:
        Findings in payload order; empty when the payload has no usable
        ``findings`` list.
    """
    raw = payload.get("findings")
    if not isinstance(raw, list):
        return ()
    parsed: list[ReviewFinding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed.append(
            ReviewFinding(
                severity=_parse_severity(item.get("severity")),
                category=str(item.get("category", "")),
                file=str(item.get("file", "")),
                line=_parse_int(item.get("line")),
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                cause="",
                fix="",
                confidence=str(item.get("confidence", "")),
                kind=_parse_kind(item.get("kind")),
            ),
        )
    return tuple(parsed)


def records_for(*, findings: Sequence[ReviewFinding]) -> tuple[FindingRecord, ...]:
    """Build tracked records for a finding set.

    Ordinal assignment for duplicate fingerprints is the matcher's own, so the
    harness cannot drift from production identity semantics.

    Args:
        findings: Findings to track.

    Returns:
        Records with fingerprints and ordinals assigned.
    """
    return match_findings(previous=None, findings=findings, round_number=1).records


def verdict_for(*, findings: Sequence[ReviewFinding]) -> ReviewVerdict:
    """Derive the readiness verdict implied by a finding set.

    Args:
        findings: Findings reported by one run, or a set of labels.

    Returns:
        The verdict :func:`lintro.ai.review.finding_matcher.derive_verdict`
        implies. Questions are excluded by that function, not here.
    """
    return derive_verdict(findings=records_for(findings=findings))


def fingerprints_for(*, findings: Sequence[ReviewFinding]) -> frozenset[str]:
    """Return the fingerprint set of a finding set.

    Args:
        findings: Findings to fingerprint.

    Returns:
        Frozen set of fingerprints, computed with the production hash so a
        Jaccard index and a matcher pairing agree on what "the same finding"
        means.
    """
    return frozenset(
        fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        for finding in findings
    )


def match_against(
    *,
    baseline: Sequence[ReviewFinding],
    candidate: Sequence[ReviewFinding],
) -> FindingMatchResult:
    """Match one finding set against another with the production matcher.

    The baseline is fed to the matcher as prior review state and the candidate
    as the current round, so the result reads:

    * ``carried`` + ``regressed`` — reported by both sides,
    * ``new`` — reported only by the candidate,
    * ``resolved`` — reported only by the baseline.

    Args:
        baseline: Findings acting as the reference set (a repeat run, another
            config's run, or the corpus labels).
        candidate: Findings being compared against the baseline.

    Returns:
        The matcher's per-round transitions for the pair.
    """
    previous = ReviewState(findings=records_for(findings=baseline))
    return match_findings(
        previous=previous,
        findings=candidate,
        round_number=MATCH_ROUND,
    )


def matched_count(result: FindingMatchResult) -> int:
    """Return how many findings both sides of a comparison reported.

    Args:
        result: Matcher result from :func:`match_against`.

    Returns:
        Count of carried plus regressed records. Regressions cannot occur for
        harness comparisons (no baseline record is ever resolved), but they are
        counted so a future baseline shape cannot lose matches silently.
    """
    return len(result.carried) + len(result.regressed)


def _parse_severity(value: Any) -> Severity:
    """Parse a severity label from a review payload.

    Args:
        value: Raw severity value.

    Returns:
        The parsed severity, defaulting to P1 for unrecognized input so a
        corrupted payload can never fabricate a clean verdict.
    """
    try:
        return Severity(str(value).upper())
    except ValueError:
        return Severity.P1


def _parse_kind(value: Any) -> FindingKind:
    """Parse a finding kind label from a review payload.

    Args:
        value: Raw kind value.

    Returns:
        The parsed kind, defaulting to ``FINDING``.
    """
    try:
        return FindingKind(str(value).lower())
    except ValueError:
        return FindingKind.FINDING


def _parse_int(value: Any) -> int:
    """Parse an integer line number from a review payload.

    Args:
        value: Raw line value.

    Returns:
        The parsed line number, or ``0`` when the value is not an integer.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
