"""Finding identity and the records a round builds from it.

A finding's identity is a stable fingerprint over ``file path``, ``category``
and a normalized title — deliberately *not* the line number, which drifts as
the PR evolves. :mod:`lintro.ai.review.finding_matcher` pairs rounds using
that identity; this module is only where identity is computed and where a
round's findings are turned into the records the matcher pairs.

Kept out of the matcher so it stays under the #2301 module-size ratchet. The
split follows the dependency direction that was already there: nothing here
imports the matcher, so the matcher re-exports these names and every caller
keeps importing them from where it always did.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "FINGERPRINT_LENGTH",
    "current_records",
    "duplicate_records",
    "fingerprint_for",
    "normalize_file_path",
    "normalize_title",
]

# Truncated sha256 hex digest length. 16 hex chars (64 bits) keeps the state
# blob small while making a collision within one PR effectively impossible.
FINGERPRINT_LENGTH = 16

_PUNCTUATION_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Normalize a finding title for fingerprinting.

    Lowercases, strips backticks and all other punctuation, and collapses
    whitespace runs so cosmetic rewordings of the same finding keep a stable
    identity across rounds.

    Args:
        title: Raw finding title as reported by the model.

    Returns:
        The normalized title.
    """
    folded = unicodedata.normalize("NFKC", title).casefold()
    stripped = _PUNCTUATION_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def normalize_file_path(path: str) -> str:
    """Normalize a repository-relative file path for fingerprinting.

    Args:
        path: File path as reported by the model.

    Returns:
        Path with Windows separators converted and any leading ``./`` removed.
    """
    return path.strip().replace("\\", "/").removeprefix("./")


def fingerprint_for(*, file: str, category: str, title: str) -> str:
    """Compute the stable fingerprint for a finding.

    Args:
        file: Repository-relative file path.
        category: Finding category label.
        title: Raw finding title.

    Returns:
        Truncated sha256 hex digest identifying the finding independently of
        its line number.
    """
    payload = "\x00".join(
        (
            normalize_file_path(file),
            normalize_title(category),
            normalize_title(title),
        ),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def _normalized_occurrences(
    *,
    finding: ReviewFinding,
) -> tuple[FindingOccurrence, ...]:
    """Return a finding's occurrences with their paths normalized.

    Args:
        finding: Finding whose occurrence locations are being tracked.

    Returns:
        The *explicitly reported* occurrences with file paths normalized the
        same way the fingerprint normalizes them, so a path that changes only
        in separator style does not read as a new location. Empty when the
        model reported none — the distinction matters, because a later round
        that omits the list must inherit the prior locations rather than
        appear to have fixed all but one of them.
    """
    return tuple(
        FindingOccurrence(
            file=normalize_file_path(occurrence.file),
            line=occurrence.line,
        )
        for occurrence in finding.occurrences
    )


def current_records(
    *,
    findings: Sequence[ReviewFinding],
    round_number: int,
) -> list[FindingRecord]:
    """Build fresh records for this round's findings with ordinals assigned.

    Ordinals are 1-based and assigned by first-seen line order within each
    fingerprint group, matching the ambiguity policy.

    Args:
        findings: Findings reported in the current round.
        round_number: Round number being recorded.

    Returns:
        Records in reported order, each carrying its fingerprint and ordinal.
    """
    grouped: dict[str, list[int]] = defaultdict(list)
    fingerprints: list[str] = []
    for index, finding in enumerate(findings):
        fingerprint = fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        fingerprints.append(fingerprint)
        grouped[fingerprint].append(index)

    ordinals: dict[int, int] = {}
    for indices in grouped.values():
        ordered = sorted(indices, key=lambda index: (findings[index].line, index))
        for ordinal, index in enumerate(ordered, start=1):
            ordinals[index] = ordinal

    return [
        FindingRecord(
            fingerprint=fingerprints[index],
            ordinal=ordinals[index],
            severity=finding.severity,
            category=finding.category,
            title=finding.title,
            file=normalize_file_path(finding.file),
            line=finding.line,
            status=FindingStatus.OPEN,
            since_round=round_number,
            checklist_ids=finding.checklist_ids,
            kind=finding.kind,
            occurrences=_normalized_occurrences(finding=finding),
            occurrences_total=len(finding.occurrences),
            severity_downgraded=finding.severity_downgraded,
            cross_chunk_contradiction=finding.cross_chunk_contradiction,
            description=finding.description,
            cause=finding.cause,
            fix=finding.fix,
            confidence=finding.confidence,
            origin=finding.origin,
            evidence_style=finding.evidence_style,
        )
        for index, finding in enumerate(findings)
    ]


def duplicate_records(
    *,
    findings: Sequence[ReviewFinding],
    round_number: int,
) -> list[FindingRecord]:
    """Build transient records for this round's merged-away duplicates.

    A duplicate merge (lintro-ops #37) leaves only the survivor in the finding
    list, so the losing side has no record of its own to pair with. These
    stand in for it just long enough to hold its prior record open; they are
    never persisted.

    Args:
        findings: Findings reported in the current round.
        round_number: Round number being recorded (1-based).

    Returns:
        One record per merged-away duplicate, in reported order.
    """
    return [
        FindingRecord(
            fingerprint=fingerprint_for(
                file=duplicate.file,
                category=duplicate.category,
                title=duplicate.title,
            ),
            severity=finding.severity,
            category=duplicate.category,
            title=duplicate.title,
            file=normalize_file_path(duplicate.file),
            line=duplicate.line,
            status=FindingStatus.OPEN,
            since_round=round_number,
        )
        for finding in findings
        for duplicate in finding.merged_duplicates
    ]
