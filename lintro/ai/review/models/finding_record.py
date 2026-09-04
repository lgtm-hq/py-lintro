"""Per-finding identity record persisted in the review state blob."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintro.ai.review.enums.cross_chunk_contradiction import CrossChunkContradiction
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models._coerce import coerce_int
from lintro.ai.review.models.finding_occurrence import (
    FindingOccurrence,
    parse_occurrences,
)
from lintro.ai.review.models.review_finding import Severity

__all__ = ["FindingRecord"]


@dataclass(frozen=True, slots=True)
class FindingRecord:
    """A single tracked finding, carried across review rounds.

    Identity is ``(fingerprint, ordinal)``. The fingerprint deliberately
    excludes the line number because lines drift as the PR evolves; the ordinal
    disambiguates findings that share a fingerprint within one round (for
    example two hardcoded credentials in the same file).

    Attributes:
        fingerprint: Stable hash of file path, category, and normalized title.
        ordinal: 1-based position among same-fingerprint findings, assigned by
            line order when the finding is first seen and then held for the
            finding's lifetime so its identity key stays stable.
        severity: Finding severity at the most recent sighting.
        category: Finding category label.
        title: Finding title as reported (unnormalized, for display).
        file: Repository-relative file path.
        line: Line number at the most recent sighting.
        status: Whether the finding is currently open or resolved.
        since_round: Round number in which the finding was first seen.
        resolved_sha: Head commit sha that resolved the finding. Empty while
            the finding is open.
        resolved_round: Round number in which the finding was resolved. Zero
            while the finding is open.
        inline_comment_id: GitHub inline review comment id anchoring this
            finding, when one was posted.
        regressed: True when the finding reappeared after being resolved.
        checklist_ids: Prompt checklist ids linked to this finding.
        kind: Whether the tracked entry is a finding or a question (#1925).
            Questions are tracked like findings but excluded from the derived
            verdict.
        occurrences: Locations at which the pattern was still present at the
            most recent sighting.
        occurrences_total: High-water number of occurrences ever seen for this
            pattern. Held across rounds so partial progress reads as
            ``addressed / total`` even after some locations are fixed.
        severity_downgraded: True when the P1 evidence gate downgraded the
            severity at the most recent sighting.
        cross_chunk_contradiction: Why the cross-chunk guard tagged this
            finding at the most recent sighting, persisted so a replayed
            finding keeps its tag and is never guarded a second time (#2265).
        description: Finding body text, persisted so a SIGTERM resume can
            still post an actionable inline comment.
        cause: Root-cause text from the most recent sighting.
        fix: Fix suggestion from the most recent sighting.
        confidence: Model confidence from the most recent sighting.
    """

    fingerprint: str
    ordinal: int = 1
    severity: Severity = Severity.P3
    category: str = ""
    title: str = ""
    file: str = ""
    line: int = 0
    status: FindingStatus = FindingStatus.OPEN
    since_round: int = 1
    resolved_sha: str = ""
    resolved_round: int = 0
    inline_comment_id: int | None = None
    regressed: bool = False
    checklist_ids: tuple[int, ...] = field(default_factory=tuple)
    kind: FindingKind = FindingKind.FINDING
    occurrences: tuple[FindingOccurrence, ...] = field(default_factory=tuple)
    occurrences_total: int = 0
    severity_downgraded: bool = False
    cross_chunk_contradiction: CrossChunkContradiction | None = None
    description: str = ""
    cause: str = ""
    fix: str = ""
    confidence: str = ""

    @property
    def key(self) -> str:
        """Return the composite identity key ``fingerprint#ordinal``."""
        return f"{self.fingerprint}#{self.ordinal}"

    @property
    def is_question(self) -> bool:
        """Return True when the tracked entry is a question."""
        return self.kind is FindingKind.QUESTION

    @property
    def occurrence_count(self) -> int:
        """Return how many occurrences were present at the last sighting."""
        return len(self.occurrences) or 1

    @property
    def occurrence_total(self) -> int:
        """Return the high-water occurrence count for this pattern."""
        return max(self.occurrences_total, self.occurrence_count)

    @property
    def occurrences_addressed(self) -> int:
        """Return how many occurrences of the pattern are already gone.

        Resolution is pattern-level: a record only reaches ``RESOLVED`` once
        every occurrence has disappeared, so while it is open this count is
        the partial progress surfaces render as ``14/20 addressed``.

        Returns:
            Number of occurrences addressed since the pattern's high-water
            mark; the full total once the record is resolved.
        """
        if self.status is FindingStatus.RESOLVED:
            return self.occurrence_total
        return max(self.occurrence_total - self.occurrence_count, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record for the hidden state blob.

        Returns:
            JSON-serializable mapping. Optional members are omitted when unset
            so the blob stays as small as possible.
        """
        payload: dict[str, Any] = {
            "fingerprint": self.fingerprint,
            "ordinal": self.ordinal,
            "severity": str(self.severity),
            "category": self.category,
            "title": self.title,
            "file": self.file,
            "line": self.line,
            "status": str(self.status),
            "since_round": self.since_round,
        }
        if self.status is FindingStatus.RESOLVED or self.resolved_sha:
            payload["resolved_in"] = {
                "sha": self.resolved_sha,
                "round": self.resolved_round,
            }
        if self.inline_comment_id is not None:
            payload["inline_comment_id"] = self.inline_comment_id
        if self.regressed:
            payload["regressed"] = True
        if self.checklist_ids:
            payload["checklist_ids"] = list(self.checklist_ids)
        if self.kind is not FindingKind.FINDING:
            payload["kind"] = str(self.kind)
        # A lone occurrence identical to the record's own location adds
        # nothing to the blob, but one at a *different* location is real
        # tracked state and must survive the round trip.
        anchor = (FindingOccurrence(file=self.file, line=self.line),)
        if self.occurrences and self.occurrences != anchor:
            payload["occurrences"] = [
                occurrence.to_dict() for occurrence in self.occurrences
            ]
        if self.occurrence_total > 1:
            payload["occurrences_total"] = self.occurrence_total
        if self.severity_downgraded:
            payload["severity_downgraded"] = True
        if self.cross_chunk_contradiction is not None:
            payload["cross_chunk_contradiction"] = self.cross_chunk_contradiction.value
        if self.description:
            payload["description"] = self.description
        if self.cause:
            payload["cause"] = self.cause
        if self.fix:
            payload["fix"] = self.fix
        if self.confidence:
            payload["confidence"] = self.confidence
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FindingRecord | None:
        """Rebuild a record from an untrusted state-blob mapping.

        Args:
            payload: Decoded JSON mapping for one finding.

        Returns:
            The parsed record, or ``None`` when the payload carries no usable
            fingerprint.
        """
        fingerprint = str(payload.get("fingerprint", "")).strip()
        if not fingerprint:
            return None
        resolved = payload.get("resolved_in")
        resolved_map = resolved if isinstance(resolved, dict) else {}
        comment_id = payload.get("inline_comment_id")
        return cls(
            fingerprint=fingerprint,
            ordinal=coerce_int(payload.get("ordinal"), default=1) or 1,
            severity=_parse_severity(payload.get("severity")),
            category=str(payload.get("category", "")),
            title=str(payload.get("title", "")),
            file=str(payload.get("file", "")),
            line=coerce_int(payload.get("line")),
            status=_parse_status(payload.get("status")),
            since_round=coerce_int(payload.get("since_round"), default=1) or 1,
            resolved_sha=str(resolved_map.get("sha", "")),
            resolved_round=coerce_int(resolved_map.get("round")),
            inline_comment_id=(
                coerce_int(comment_id) or None if comment_id is not None else None
            ),
            regressed=bool(payload.get("regressed", False)),
            checklist_ids=_parse_checklist_ids(payload.get("checklist_ids")),
            kind=_parse_kind(payload.get("kind")),
            occurrences=parse_occurrences(payload.get("occurrences")),
            occurrences_total=coerce_int(payload.get("occurrences_total")),
            severity_downgraded=bool(payload.get("severity_downgraded", False)),
            cross_chunk_contradiction=_contradiction_from_payload(
                payload.get("cross_chunk_contradiction"),
            ),
            description=str(payload.get("description", "")),
            cause=str(payload.get("cause", "")),
            fix=str(payload.get("fix", "")),
            confidence=str(payload.get("confidence", "")),
        )


def _parse_checklist_ids(value: Any) -> tuple[int, ...]:
    """Parse checklist ids from an untrusted state-blob value.

    Args:
        value: Raw value decoded from the state blob.

    Returns:
        Tuple of integer ids; empty when the value is not a list of numbers.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        item for item in value if isinstance(item, int) and not isinstance(item, bool)
    )


def _parse_severity(value: Any) -> Severity:
    """Parse a severity label, failing closed to P1 for unknown input.

    An unrecognizable severity must never quietly become the least-blocking
    label: derive_verdict only escalates to BLOCKED on a P1, so downgrading a
    corrupted or renamed severity to P3 would fabricate a clean verdict for a
    finding that may have been a P1.
    """
    try:
        return Severity(str(value).upper())
    except ValueError:
        return Severity.P1


def _parse_kind(value: Any) -> FindingKind:
    """Parse a finding kind label from an untrusted state blob.

    An unrecognized or absent label becomes ``FINDING``: a v1/v2 record
    carries no kind key, and mistaking a question for a finding merely renders
    it with a severity, whereas the reverse would drop a real defect out of
    the derived verdict.

    Args:
        value: Raw kind value decoded from the state blob.

    Returns:
        The parsed kind, defaulting to :data:`FindingKind.FINDING`.
    """
    try:
        return FindingKind(str(value).lower())
    except ValueError:
        return FindingKind.FINDING


def _parse_status(value: Any) -> FindingStatus:
    """Parse a finding status label, defaulting to open for unknown input."""
    try:
        return FindingStatus(str(value).lower())
    except ValueError:
        return FindingStatus.OPEN


def _contradiction_from_payload(value: object) -> CrossChunkContradiction | None:
    """Parse a persisted cross-chunk tag, tolerating older states without one.

    Args:
        value: Raw payload value, ``None`` for states written before the tag.

    Returns:
        The tag, or ``None`` when absent or unrecognized.
    """
    if not isinstance(value, str):
        return None
    try:
        return CrossChunkContradiction(value)
    except ValueError:
        return None
