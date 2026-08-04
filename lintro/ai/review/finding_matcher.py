"""Cross-run finding identity and matching for AI review state.

Findings are identified by a stable fingerprint over ``file path``,
``category``, and a normalized title — deliberately *not* the line number,
which drifts as the PR evolves. Two findings can legitimately share a
fingerprint within a single round (two hardcoded credentials in one file), so
identity is the pair ``(fingerprint, ordinal)`` where the ordinal is assigned
by first-seen line order.

On later rounds ambiguous candidates are paired by nearest-line distance. When
the pairing is still ambiguous the matcher biases toward *carrying over* an
open finding rather than declaring it resolved: a stale open finding is a
lesser failure than a false "Addressed" banner.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "FINGERPRINT_LENGTH",
    "derive_verdict",
    "fingerprint_for",
    "match_findings",
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


def derive_verdict(*, findings: Iterable[FindingRecord]) -> ReviewVerdict:
    """Derive the merge-readiness verdict from open findings.

    Args:
        findings: Tracked finding records; resolved records are ignored.

    Returns:
        The readiness verdict implied by the open findings' severities.
        Questions (#1925) are excluded: they carry no severity semantics and
        must never move the verdict.
    """
    severities = {
        record.severity
        for record in findings
        if record.status is FindingStatus.OPEN and not record.is_question
    }
    if Severity.P1 in severities:
        return ReviewVerdict.BLOCKED
    if Severity.P2 in severities:
        return ReviewVerdict.CHANGES_REQUESTED
    if Severity.P3 in severities:
        return ReviewVerdict.NITS_ONLY
    return ReviewVerdict.READY


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


def _current_records(
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
        )
        for index, finding in enumerate(findings)
    ]


def _pair_group(
    *,
    prior: Sequence[FindingRecord],
    current: Sequence[FindingRecord],
) -> dict[int, int]:
    """Pair current records to prior records within one fingerprint group.

    Candidate pairs are ranked by absolute line distance; ties prefer a prior
    record that is still open, so an ambiguous match carries a finding over
    rather than declaring it resolved.

    Args:
        prior: Prior records sharing the fingerprint.
        current: Current-round records sharing the fingerprint.

    Returns:
        Mapping of current index to prior index for the chosen pairs.
    """
    candidates = [
        (
            abs(current_record.line - prior_record.line),
            0 if prior_record.status is FindingStatus.OPEN else 1,
            abs(current_record.ordinal - prior_record.ordinal),
            prior_index,
            current_index,
        )
        for current_index, current_record in enumerate(current)
        for prior_index, prior_record in enumerate(prior)
    ]
    candidates.sort()

    pairs: dict[int, int] = {}
    used_prior: set[int] = set()
    for _distance, _open_first, _ordinal_gap, prior_index, current_index in candidates:
        if current_index in pairs or prior_index in used_prior:
            continue
        pairs[current_index] = prior_index
        used_prior.add(prior_index)
    return pairs


def _next_free_ordinal(*, taken: set[int]) -> int:
    """Return the lowest 1-based ordinal not already used in a group.

    Args:
        taken: Ordinals already claimed by records sharing the fingerprint.

    Returns:
        The smallest unused ordinal.
    """
    ordinal = 1
    while ordinal in taken:
        ordinal += 1
    return ordinal


def _merge_pair(
    *,
    prior: FindingRecord,
    current: FindingRecord,
) -> tuple[FindingRecord, FindingMatchOutcome]:
    """Merge a matched prior record with its current-round sighting.

    A finding with several occurrences is one pattern, not one finding per
    location, so the merged record keeps this round's surviving occurrences
    while holding the high-water total. Fixing 6 of 20 call sites therefore
    reads as partial progress on an open finding, and only the disappearance
    of the whole pattern resolves it.

    Args:
        prior: Previously tracked record.
        current: Freshly built record for this round.

    Returns:
        Tuple of the merged record and the transition it represents.
    """
    regressed = prior.status is FindingStatus.RESOLVED
    merged = FindingRecord(
        fingerprint=prior.fingerprint,
        # The ordinal is part of the persistent identity: a matched finding
        # keeps the one it was first assigned, so its key stays stable and can
        # never collide with a sibling still tracked under the old ordinal.
        ordinal=prior.ordinal,
        severity=current.severity,
        category=current.category,
        title=current.title,
        file=current.file,
        line=current.line,
        status=FindingStatus.OPEN,
        since_round=prior.since_round,
        resolved_sha=prior.resolved_sha,
        resolved_round=prior.resolved_round,
        inline_comment_id=prior.inline_comment_id,
        regressed=regressed or prior.regressed,
        checklist_ids=current.checklist_ids or prior.checklist_ids,
        kind=current.kind,
        # A round that reports no occurrence list is not a claim that the
        # pattern shrank to one location — it is silence, so the previously
        # tracked locations are carried rather than treated as progress.
        occurrences=current.occurrences or prior.occurrences,
        occurrences_total=max(prior.occurrence_total, current.occurrence_total),
        severity_downgraded=current.severity_downgraded,
    )
    if regressed:
        return merged, FindingMatchOutcome.REGRESSED
    return merged, FindingMatchOutcome.CARRIED


def match_findings(
    *,
    previous: ReviewState | None,
    findings: Sequence[ReviewFinding],
    round_number: int,
    head_sha: str = "",
) -> FindingMatchResult:
    """Match this round's findings against the previously persisted state.

    Every current finding is classified as ``new``, ``carried``, or
    ``regressed``; every prior open finding absent from this round is marked
    ``resolved`` and stamped with the head sha and round that resolved it.

    Resolution is pattern-level (#1925): a finding reported at several
    occurrences resolves only when the whole pattern stops being reported.
    Fixing some of its locations leaves it open with a lower
    ``occurrence_count`` against an unchanged ``occurrence_total``.

    Args:
        previous: State decoded from the prior sticky comment, or ``None`` for
            the first round on a PR.
        findings: Findings reported in the current round.
        round_number: Round number being recorded (1-based).
        head_sha: Head commit sha reviewed in this round; stamped onto findings
            resolved by this round.

    Returns:
        The per-round transitions plus the merged record set to persist.
    """
    prior_records = list(previous.findings) if previous is not None else []
    current_records = _current_records(findings=findings, round_number=round_number)

    prior_by_fingerprint: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(prior_records):
        prior_by_fingerprint[record.fingerprint].append(index)
    current_by_fingerprint: dict[str, list[FindingRecord]] = defaultdict(list)
    for record in current_records:
        current_by_fingerprint[record.fingerprint].append(record)

    merged: list[FindingRecord] = []
    new: list[FindingRecord] = []
    carried: list[FindingRecord] = []
    regressed: list[FindingRecord] = []
    resolved: list[FindingRecord] = []
    outcomes: dict[str, FindingMatchOutcome] = {}
    matched_prior: set[int] = set()

    for fingerprint, group in current_by_fingerprint.items():
        prior_indices = prior_by_fingerprint.get(fingerprint, [])
        prior_group = [prior_records[index] for index in prior_indices]
        pairs = _pair_group(prior=prior_group, current=group)
        # Every prior record of this fingerprint stays in state (matched, or
        # carried as resolved), so their ordinals remain taken.
        taken = {record.ordinal for record in prior_group}
        assigned: dict[int, FindingRecord] = {}

        for current_index, record in enumerate(group):
            group_index = pairs.get(current_index)
            if group_index is None:
                continue
            prior_record = prior_group[group_index]
            matched_prior.add(prior_indices[group_index])
            updated, outcome = _merge_pair(prior=prior_record, current=record)
            assigned[current_index] = updated
            outcomes[updated.key] = outcome
            if outcome is FindingMatchOutcome.REGRESSED:
                regressed.append(updated)
            else:
                carried.append(updated)

        unmatched = sorted(
            (index for index in range(len(group)) if index not in assigned),
            key=lambda index: (group[index].line, index),
        )
        for current_index in unmatched:
            ordinal = _next_free_ordinal(taken=taken)
            taken.add(ordinal)
            record = replace(group[current_index], ordinal=ordinal)
            assigned[current_index] = record
            new.append(record)
            outcomes[record.key] = FindingMatchOutcome.NEW

        merged.extend(assigned[index] for index in range(len(group)))

    for index, record in enumerate(prior_records):
        if index in matched_prior:
            continue
        if record.status is FindingStatus.RESOLVED:
            merged.append(record)
            continue
        closed = replace(
            record,
            status=FindingStatus.RESOLVED,
            resolved_sha=head_sha,
            resolved_round=round_number,
        )
        merged.append(closed)
        resolved.append(closed)
        outcomes[closed.key] = FindingMatchOutcome.RESOLVED

    return FindingMatchResult(
        records=tuple(merged),
        new=tuple(new),
        carried=tuple(carried),
        resolved=tuple(resolved),
        regressed=tuple(regressed),
        outcomes=outcomes,
    )
