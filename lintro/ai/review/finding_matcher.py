"""Cross-run finding identity and matching for AI review state.

Findings are identified by a stable fingerprint over ``file path``,
``category``, and a normalized title — deliberately *not* the line number,
which drifts as the PR evolves. Two findings can legitimately share a
fingerprint within a single round (two hardcoded credentials in one file), so
identity is the pair ``(fingerprint, ordinal)`` where the ordinal is assigned
by first-seen line order. Identity itself, and the records a round builds from
it, live in :mod:`lintro.ai.review.finding_identity`; this module pairs them
across rounds and re-exports those names for callers.

On later rounds ambiguous candidates are paired by nearest-line distance. When
the pairing is still ambiguous the matcher biases toward *carrying over* an
open finding rather than declaring it resolved: a stale open finding is a
lesser failure than a false "Addressed" banner.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_identity import (
    FINGERPRINT_LENGTH,
    current_records,
    duplicate_records,
    fingerprint_for,
    normalize_file_path,
    normalize_title,
)
from lintro.ai.review.finding_pairing import (
    duplicates_holding_prior_records,
    merge_pair,
    next_free_ordinal,
    notes_holding_prior_records,
    pair_group,
)
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.posting_tiers import is_inline_tier

__all__ = [
    "FINGERPRINT_LENGTH",
    "count_blocking_findings",
    "derive_verdict",
    "fingerprint_for",
    "match_findings",
    "normalize_file_path",
    "normalize_title",
    "review_findings_from_unposted",
]


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


def count_blocking_findings(*, findings: Iterable[FindingRecord]) -> int:
    """Count the open findings that block merge readiness.

    The single definition of "blocking" shared by :func:`derive_verdict` and
    every surface that has to report the same thing in a number rather than a
    verdict — notably the converged-skip envelope and its sticky banner
    (#2099). Keeping one predicate is the point: a copy that drifted would let
    the board and the verdict disagree about what is holding a PR.

    Args:
        findings: Tracked finding records; resolved records are ignored.

    Returns:
        Number of open, non-question P1 records. Questions are excluded for
        the same reason :func:`derive_verdict` excludes them — an open
        question is a request for information, not a defect claim.
    """
    return sum(
        1
        for record in findings
        if record.status is FindingStatus.OPEN
        and not record.is_question
        and record.severity is Severity.P1
    )


def review_findings_from_unposted(
    *,
    prior: ReviewState,
    current: Sequence[ReviewFinding],
    reviewed_paths: frozenset[str],
) -> tuple[ReviewFinding, ...]:
    """Rebuild findings for open prior records that never got an inline post.

    A SIGTERM after a coverage checkpoint leaves ``FindingRecord``s with no
    ``inline_comment_id``. Resume then skips COVERED files and would
    otherwise never post those issues. Records for files this run
    re-reviewed are omitted so absence can resolve them. Records outside the
    inline severity tier are omitted too: a P3 never had a thread to miss,
    and it is already listed in the sticky (lintro-ops #37).

    Args:
        prior: Artifact state loaded for this resume.
        current: Findings already produced by this run.
        reviewed_paths: Paths this run actually read.

    Returns:
        Reconstructed findings that should be posted this round.
    """
    seen = {
        fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        for finding in current
    }
    extra: list[ReviewFinding] = []
    for record in prior.findings:
        if record.status is not FindingStatus.OPEN:
            continue
        if record.inline_comment_id is not None:
            continue
        if not is_inline_tier(severity=record.severity):
            continue
        if record.fingerprint in seen:
            continue
        path = normalize_file_path(record.file)
        if path in reviewed_paths or record.file in reviewed_paths:
            continue
        if not (record.description or record.cause or record.fix):
            continue
        extra.append(review_finding_from_record(record=record))
    return tuple(extra)


def review_finding_from_record(*, record: FindingRecord) -> ReviewFinding:
    """Rebuild the finding a tracked record was made from.

    Copies the fields a record persists: severity, category, file, line,
    title, description, cause, fix, confidence, checklist ids, kind,
    occurrences, the P1 downgrade flag, the cross-chunk tag, origin and
    evidence style. ``description`` and ``confidence`` fall back to the title
    and ``medium`` because a record written before those fields existed leaves
    them empty.

    The rebuild is **not** a round trip. ``suggested_change``,
    ``suggested_code``, ``failure_scenario`` and ``source`` are not persisted
    on a record, so they come back at their defaults — a rebuilt finding can
    render prose (the agent prompt, the sticky's folded detail) but never a
    committable suggestion block.

    Args:
        record: The tracked record to rebuild.

    Returns:
        ReviewFinding: The finding the record describes, minus the fields a
        record does not persist.
    """
    return ReviewFinding(
        severity=record.severity,
        category=record.category,
        file=record.file,
        line=record.line,
        title=record.title,
        description=record.description or record.title,
        cause=record.cause,
        fix=record.fix,
        confidence=record.confidence or "medium",
        checklist_ids=record.checklist_ids,
        kind=record.kind,
        occurrences=record.occurrences,
        severity_downgraded=record.severity_downgraded,
        cross_chunk_contradiction=record.cross_chunk_contradiction,
        origin=record.origin,
        evidence_style=record.evidence_style,
    )


def match_findings(
    *,
    previous: ReviewState | None,
    findings: Sequence[ReviewFinding],
    round_number: int,
    head_sha: str = "",
    reviewed_paths: frozenset[str] | None = None,
    departed_paths: frozenset[str] | None = None,
) -> FindingMatchResult:
    """Match this round's findings against the previously persisted state.

    Every current finding is classified as ``new``, ``carried``, or
    ``regressed``; every prior open finding absent from this round is marked
    ``resolved`` and stamped with the head sha and round that resolved it.

    Resolution is pattern-level (#1925): a finding reported at several
    occurrences resolves only when the whole pattern stops being reported.
    Fixing some of its locations leaves it open with a lower
    ``occurrence_count`` against an unchanged ``occurrence_total``.

    A finding the posting policy routed to the notes block (#2572,
    ``posted_inline`` cleared) gets no record of its own: it opens no thread,
    so there is nothing to carry, resolve, or count as open in a later round.
    A prior *inline* record a note re-asserts is not resolved either — the
    model still asserts the finding, only below the floor — so it is carried
    forward open rather than stamped fixed. Notes are paired to prior records
    one by one
    (:func:`~lintro.ai.review.finding_pairing.notes_holding_prior_records`), so
    a sibling sharing the fingerprint that stopped being reported still
    resolves.

    A finding a duplicate merge folded into another (lintro-ops #37) is the
    same case seen from the other side: the merge re-attributes the defect to
    its root cause rather than repairing it, so the merged-away finding's
    prior record is carried forward open too, paired one by one by
    (:func:`~lintro.ai.review.finding_pairing.duplicates_holding_prior_records`).

    The filter lives here rather than at each caller so the sticky, the
    review body, the inline comments, and the coverage bookkeeping cannot
    disagree about which findings exist.

    Args:
        previous: State decoded from the prior sticky comment, or ``None`` for
            the first round on a PR.
        findings: Findings reported in the current round. Entries with
            ``posted_inline`` cleared get no record, but keep a matching
            prior record open.
        round_number: Round number being recorded (1-based).
        head_sha: Head commit sha reviewed in this round; stamped onto findings
            resolved by this round.
        reviewed_paths: Files re-reviewed this round. Open findings on other
            paths carry forward. ``None`` keeps the legacy resolve-on-absence
            behavior.
        departed_paths: Paths that left the diff (deletes and rename sources)
            and may resolve even when they were not re-reviewed.

    Returns:
        The per-round transitions plus the merged record set to persist.
    """
    prior_records = list(previous.findings) if previous is not None else []
    inline_records = current_records(
        findings=[finding for finding in findings if finding.posted_inline],
        round_number=round_number,
    )
    note_records = current_records(
        findings=[finding for finding in findings if not finding.posted_inline],
        round_number=round_number,
    )

    prior_by_fingerprint: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(prior_records):
        prior_by_fingerprint[record.fingerprint].append(index)
    current_by_fingerprint: dict[str, list[FindingRecord]] = defaultdict(list)
    for record in inline_records:
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
        pairs = pair_group(prior=prior_group, current=group)
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
            updated, outcome = merge_pair(prior=prior_record, current=record)
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
            ordinal = next_free_ordinal(taken=taken)
            taken.add(ordinal)
            record = replace(group[current_index], ordinal=ordinal)
            assigned[current_index] = record
            new.append(record)
            outcomes[record.key] = FindingMatchOutcome.NEW

        merged.extend(assigned[index] for index in range(len(group)))

    note_held, note_carries = notes_holding_prior_records(
        prior_records=prior_records,
        prior_by_fingerprint=prior_by_fingerprint,
        notes=note_records,
        matched_prior=matched_prior,
    )
    # A record a note already holds is not available to a merged duplicate:
    # one held record needs one reason, not two.
    duplicate_held = duplicates_holding_prior_records(
        prior_records=prior_records,
        prior_by_fingerprint=prior_by_fingerprint,
        duplicates=duplicate_records(findings=findings, round_number=round_number),
        matched_prior=matched_prior | note_held,
    )

    for index, record in enumerate(prior_records):
        if index in matched_prior:
            continue
        if record.status is FindingStatus.RESOLVED:
            merged.append(record)
            continue
        path = record.file
        left_diff = departed_paths is not None and path in departed_paths
        unread = reviewed_paths is not None and path not in reviewed_paths
        held = index in note_held or index in duplicate_held
        if (unread and not left_diff) or held:
            merged.append(record)
            carried.append(record)
            outcomes[record.key] = FindingMatchOutcome.CARRIED
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
        note_carries=note_carries,
    )
