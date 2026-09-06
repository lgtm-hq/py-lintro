"""Pairing and merging of prior and current finding records.

Split out of :mod:`lintro.ai.review.finding_matcher` (#2301). The matcher owns
identity, record construction and the round-level walk; the per-fingerprint
pairing rule and the prior/current merge live here. Both were moved verbatim,
so the ambiguity bias is unchanged: an ambiguous pair carries an open finding
over rather than declaring it resolved.
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.finding_record import FindingRecord

__all__ = [
    "merge_pair",
    "next_free_ordinal",
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
