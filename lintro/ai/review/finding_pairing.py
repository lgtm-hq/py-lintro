"""Pairing and merging of prior and current finding records.

Split out of :mod:`lintro.ai.review.finding_matcher` (#2301). The matcher owns
identity, record construction and the round-level walk; the per-fingerprint
pairing rule and the prior/current merge live here. Both were moved verbatim,
so the ambiguity bias is unchanged: an ambiguous pair carries an open finding
over rather than declaring it resolved.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.finding_record import FindingRecord

__all__ = [
    "duplicates_holding_prior_records",
    "merge_pair",
    "next_free_ordinal",
    "notes_holding_prior_records",
    "pair_group",
]


def pair_group(
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


def next_free_ordinal(*, taken: set[int]) -> int:
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


def merge_pair(
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
        cross_chunk_contradiction=current.cross_chunk_contradiction,
        description=current.description or prior.description,
        cause=current.cause or prior.cause,
        fix=current.fix or prior.fix,
        confidence=current.confidence or prior.confidence,
        # Provenance belongs to the first sighting and is set only when a
        # record is created: a cross-chunk finding stays attributed to the
        # synthesis pass even on a later round where an ordinary chunk
        # reported it too, and — symmetrically — a chunk-first record is not
        # retroactively re-attributed to the synthesis pass by a later round.
        origin=prior.origin,
        # A carried finding keeps the *evidence basis* it was first scored on,
        # so the likelihood term cannot drift on label noise alone. Severity
        # and confidence still follow the current round, so the numeric score
        # can still move — the freeze is on the basis, not on the score. A
        # regressed finding is a fresh sighting and is re-scored (#2099).
        evidence_style=current.evidence_style if regressed else prior.evidence_style,
    )
    if regressed:
        return merged, FindingMatchOutcome.REGRESSED
    return merged, FindingMatchOutcome.CARRIED


def notes_holding_prior_records(
    *,
    prior_records: Sequence[FindingRecord],
    prior_by_fingerprint: Mapping[str, list[int]],
    notes: Sequence[FindingRecord],
    matched_prior: AbstractSet[int],
) -> tuple[set[int], frozenset[tuple[str, int]]]:
    """Pair this round's notes with the prior records they keep open (#2572).

    A note is not a record of its own, but it is still the model asserting the
    finding — only below the inline confidence floor, or as a question the
    policy does not post. The prior record it re-asserts is therefore carried
    rather than resolved.

    The pairing is per record, not per fingerprint: two prior records can share
    a fingerprint at different lines and ordinals, so carrying every sibling
    because one of them came back as a note would leave the absent sibling open
    forever. Notes are paired against the prior records no current *inline*
    finding claimed, by the same :func:`pair_group` rules, so each note holds at
    most one record open.

    Args:
        prior_records: Records decoded from the prior state, in state order.
        prior_by_fingerprint: Index of ``prior_records`` positions per
            fingerprint.
        notes: Transient records built from this round's notes.
        matched_prior: Positions already claimed by an inline finding.

    Returns:
        The prior positions notes hold open, and the ``(fingerprint, line)`` of
        each note that holds one whose thread was actually posted — what the
        sticky tags.
    """
    held_by = _held_prior_records(
        prior_records=prior_records,
        prior_by_fingerprint=prior_by_fingerprint,
        current=notes,
        matched_prior=matched_prior,
    )
    # Only a record whose thread was posted can be the reason a thread is
    # still open, which is what the sticky's tag tells the reader.
    carries = {
        (prior_records[index].fingerprint, note.line)
        for index, note in held_by.items()
        if prior_records[index].inline_comment_id is not None
    }
    return set(held_by), frozenset(carries)


def duplicates_holding_prior_records(
    *,
    prior_records: Sequence[FindingRecord],
    prior_by_fingerprint: Mapping[str, list[int]],
    duplicates: Sequence[FindingRecord],
    matched_prior: AbstractSet[int],
) -> set[int]:
    """Pair merged-away duplicates with the prior records they keep open.

    A duplicate merge (lintro-ops #37) drops the losing side from the round's
    finding list and folds its sites into the survivor. The defect it named is
    still live — the synthesis pass re-attributed it, it did not fix it — so
    the record it opened is carried rather than resolved.

    The pairing is per record for the same reason it is in
    :func:`notes_holding_prior_records`: two prior records can share a
    fingerprint at different lines, and carrying every sibling because one of
    them was merged away would leave the absent sibling open forever.

    Args:
        prior_records: Records decoded from the prior state, in state order.
        prior_by_fingerprint: Index of ``prior_records`` positions per
            fingerprint.
        duplicates: Transient records built from this round's merged-away
            duplicates.
        matched_prior: Positions already claimed by a current finding or held
            by a note.

    Returns:
        The prior positions merged-away duplicates hold open.
    """
    return set(
        _held_prior_records(
            prior_records=prior_records,
            prior_by_fingerprint=prior_by_fingerprint,
            current=duplicates,
            matched_prior=matched_prior,
        ),
    )


def _held_prior_records(
    *,
    prior_records: Sequence[FindingRecord],
    prior_by_fingerprint: Mapping[str, list[int]],
    current: Sequence[FindingRecord],
    matched_prior: AbstractSet[int],
) -> dict[int, FindingRecord]:
    """Pair transient records against the open prior records they re-assert.

    Shared by the notes and duplicate-merge paths: both are the model still
    asserting a finding without opening a record of its own, so both hold a
    prior record open by the same :func:`pair_group` rules.

    Args:
        prior_records: Records decoded from the prior state, in state order.
        prior_by_fingerprint: Index of ``prior_records`` positions per
            fingerprint.
        current: Transient records to pair, in reported order.
        matched_prior: Positions already claimed and therefore unavailable.

    Returns:
        Mapping of held prior position to the transient record holding it.
    """
    by_fingerprint: dict[str, list[FindingRecord]] = defaultdict(list)
    for record in current:
        by_fingerprint[record.fingerprint].append(record)

    held: dict[int, FindingRecord] = {}
    for fingerprint, group in by_fingerprint.items():
        available = [
            index
            for index in prior_by_fingerprint.get(fingerprint, [])
            if index not in matched_prior
            and prior_records[index].status is FindingStatus.OPEN
        ]
        prior_group = [prior_records[index] for index in available]
        for current_index, group_index in pair_group(
            prior=prior_group,
            current=group,
        ).items():
            held[available[group_index]] = group[current_index]
    return held
