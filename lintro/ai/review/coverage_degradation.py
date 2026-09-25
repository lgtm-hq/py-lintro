"""Shared wording for coverage limits a review run recorded (#2003).

Every surface (terminal, GitHub review body, sticky comment) describes a
degraded run with the same sentence built here, so a degraded review can never
read as complete on one surface and limited on another.

There is no per-call findings cap to describe (lintro-ops milestone 0,
decision A): the per-chunk limits are an output-exhaustion split and a failed
optional depth pass, and the whole-run limits are the synthesis pass's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    SYNTHESIS_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)

if TYPE_CHECKING:
    from lintro.ai.review.models.review_metadata import ReviewMetadata

__all__ = [
    "COVERAGE_LIMITED_HEADLINE",
    "PARTIAL_REVIEW_LABEL",
    "CARRIED_SYNTHESIS_NOTE",
    "CARRIED_VERIFICATION_NOTE",
    "GENERATED_QUESTIONS_FAILED_NOTE",
    "describe_coverage_degradations",
    "format_narrative_note",
]

#: Short label reused as the bold lead-in on the posted GitHub surfaces.
COVERAGE_LIMITED_HEADLINE = "Coverage limited — not a guaranteed full finding set"

#: The note a failed per-PR question pass leaves (#2720, #2803). The pass
#: runs once per run and every chunk was still reviewed against the full
#: rubric, so it is a depth note beside the synthesis and verification notes,
#: never the coverage-limited warning. The CI classifier mirrors this text
#: for its ``::warning::`` (a contract test pins the pair).
GENERATED_QUESTIONS_FAILED_NOTE = (
    "The per-PR question pass failed, so every chunk was reviewed against "
    "the rubric alone."
)

#: Notes for a synthesis or verification failure an earlier round recorded
#: and this round carries without running the pass again (#2803).
CARRIED_SYNTHESIS_NOTE = (
    "The synthesis pass did not complete on the round this one repeats, so "
    "cross-chunk duplicate merging was not applied."
)
CARRIED_VERIFICATION_NOTE = (
    "Verification did not complete on the round this one repeats, so the "
    "selected findings stay unverified."
)

#: What a degraded run is called in the *header* of each posted surface
#: (#2395). The warning below it explains why; the header exists so a reader
#: who never scrolls past the first line still learns the review is partial,
#: and so it matches the ``degraded`` outcome the CI check reports.
PARTIAL_REVIEW_LABEL = "Partial review"

#: How a depth-3 pass failure is named in the sentence (#2395). It is a
#: per-chunk reason that carries no per-call ceiling, so it gets its own
#: clause rather than joining the cap wording.
_DEPTH_PASS_CLAUSES: dict[CoverageDegradationReason, str] = {
    CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED: (
        "the depth-3 adversarial sweep failed"
    ),
}

#: How a per-file reason carried from the attempt a round reruns is named in
#: the "not redone" clause (#2803), in the words its own clause uses.
_NOT_REDONE_LABELS: dict[CoverageDegradationReason, str] = {
    CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED: (
        "a chunk split after exhausting the output limit"
    ),
    CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED: (
        "a failed depth-3 adversarial sweep"
    ),
    CoverageDegradationReason.SPLIT_HALF_FAILED: "a split chunk's lost half",
    CoverageDegradationReason.TURN_LIMIT_REACHED: "a chunk that hit the turn limit",
}


def _not_redone_label(*, item: CoverageDegradation) -> str:
    """Name a carried per-file reason for the "not redone" clause.

    Args:
        item: The carried degradation.

    Returns:
        The label its own clause uses; an unchanged single-file retry is
        not called a split, and a reason without a label reads as words.
    """
    if (
        item.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
        and not item.split
    ):
        return "a single-file chunk retried after exhausting the output limit"
    return _NOT_REDONE_LABELS.get(item.reason, str(item.reason).replace("_", " "))


def _plural(*, count: int, noun: str) -> str:
    """Return ``noun`` pluralized for ``count``.

    Args:
        count: Number of items.
        noun: Singular noun.

    Returns:
        The noun with an ``s`` appended unless the count is exactly one.
    """
    return noun if count == 1 else f"{noun}s"


def describe_coverage_degradations(*, metadata: ReviewMetadata) -> str:
    """Describe why a run's finding set may be incomplete.

    Args:
        metadata: Review run metadata carrying ``coverage_degradations``.

    Returns:
        A plain-text sentence naming how many chunks were split after output
        exhaustion and any incomplete optional pass, or an empty string when
        the run recorded no degradation. The text carries no markup so the
        terminal and the GitHub surfaces can share it verbatim.
    """
    # The narrative reasons have their own notes (synthesis, verification,
    # the question pass) and never make the finding set incomplete (#2702,
    # #2803).
    recorded = tuple(
        item
        for item in metadata.coverage_degradations
        if item.reason not in NARRATIVE_DEGRADATION_REASONS
    )
    if not recorded:
        return ""
    # A per-file reason carried from the attempt this round reruns (#2803)
    # belongs to no chunk of this round; it gets its own clause below and
    # stays out of the per-chunk counts. A carried cut keeps its own wording.
    not_redone = sorted(
        {
            _not_redone_label(item=item)
            for item in recorded
            if item.chunk_index == CARRIED_CHUNK_INDEX
            and item.reason is not CoverageDegradationReason.DIFF_TRUNCATED
        },
    )
    degradations = tuple(
        item
        for item in recorded
        if item.chunk_index != CARRIED_CHUNK_INDEX
        or item.reason is CoverageDegradationReason.DIFF_TRUNCATED
    )

    retried = [
        item
        for item in degradations
        if item.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
    ]
    # Rows are per limit event, not per chunk: a chunk whose depth pass also
    # failed contributes two rows with one chunk_index. Count chunks by
    # distinct index and never let the row count inflate the denominator.
    # A whole-run degradation carries the synthesis sentinel rather than a
    # real chunk index, so it must not be counted as a chunk either: without
    # this, a single-chunk run whose synthesis pass was truncated would read
    # as "1 of 2 chunks".
    affected = {item.chunk_index for item in degradations if item.chunk_index >= 0}
    total = max(metadata.chunks_total, len(affected))

    clauses: list[str] = []
    # A single-file chunk cannot be split, so it is retried once unchanged and
    # keeps the whole-chunk view a split gives up. Reporting both as splits
    # would claim a loss of context the unchanged retry never took.
    split_chunks = len({item.chunk_index for item in retried if item.split})
    unsplit_chunks = len({item.chunk_index for item in retried if not item.split})
    if split_chunks:
        clauses.append(
            f"{split_chunks} of {total} {_plural(count=total, noun='chunk')} "
            "exhausted the provider output limit and "
            f"{'was' if split_chunks == 1 else 'were'} split and re-reviewed "
            "in halves",
        )
    if unsplit_chunks:
        clauses.append(
            f"{unsplit_chunks} of {total} single-file "
            f"{_plural(count=unsplit_chunks, noun='chunk')} exhausted the "
            "provider output limit and "
            f"{'was' if unsplit_chunks == 1 else 'were'} retried once "
            "unchanged",
        )

    for reason, wording in _DEPTH_PASS_CLAUSES.items():
        chunks = {item.chunk_index for item in degradations if item.reason is reason}
        if chunks:
            clauses.append(
                f"{len(chunks)} {_plural(count=len(chunks), noun='chunk')} kept "
                f"only the main pass after {wording}",
            )

    truncated = [
        item
        for item in degradations
        if item.reason is CoverageDegradationReason.DIFF_TRUNCATED
    ]
    cut = {item.chunk_index for item in truncated if item.chunk_index >= 0}
    if cut:
        clauses.append(
            f"{len(cut)} of {total} {_plural(count=total, noun='chunk')} had "
            f"{'its' if len(cut) == 1 else 'their'} diff cut to the context "
            "window, so only a prefix of each affected file's change was "
            "reviewed",
        )
    carried = sum(1 for item in truncated if item.chunk_index == CARRIED_CHUNK_INDEX)
    if carried:
        clauses.append(
            f"{carried} {_plural(count=carried, noun='file')} carried from an "
            "earlier round had only a prefix of "
            f"{'its' if carried == 1 else 'their'} diff reviewed; a change to "
            "the file re-reviews it",
        )

    lost_half = {
        item.chunk_index
        for item in degradations
        if item.reason is CoverageDegradationReason.SPLIT_HALF_FAILED
    }
    if lost_half:
        clauses.append(
            f"{len(lost_half)} split {_plural(count=len(lost_half), noun='chunk')} "
            "lost one half to a failed call, so the files in "
            f"{'that half' if len(lost_half) == 1 else 'those halves'} were "
            "not reviewed",
        )

    turn_limited_items = [
        item
        for item in degradations
        if item.reason is CoverageDegradationReason.TURN_LIMIT_REACHED
    ]
    turn_limited = {item.chunk_index for item in turn_limited_items}
    if turn_limited:
        limits = sorted({item.limit for item in turn_limited_items if item.limit})
        named = f" ({limits[0]} turns)" if len(limits) == 1 else ""
        clauses.append(
            f"{len(turn_limited)} {_plural(count=len(turn_limited), noun='chunk')} "
            f"hit the per-call turn limit{named} before answering, so "
            f"{'its' if len(turn_limited) == 1 else 'their'} files were left "
            "unreviewed",
        )

    if not_redone:
        clauses.append(
            "work the previous round degraded was not redone "
            f"({', '.join(not_redone)}); it is redone when these files are reviewed "
            "again",
        )

    known = {
        CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        CoverageDegradationReason.DIFF_TRUNCATED,
        CoverageDegradationReason.SPLIT_HALF_FAILED,
        CoverageDegradationReason.TURN_LIMIT_REACHED,
        *_DEPTH_PASS_CLAUSES,
    }
    other = sorted(
        {str(item.reason) for item in degradations if item.reason not in known},
    )
    if other:
        # A reason this describer does not yet know still gets a clause, so a
        # new enum member can never render an empty, leading-period sentence.
        clauses.append(
            f"{len(other)} other {_plural(count=len(other), noun='limit')} "
            f"applied ({', '.join(other)})",
        )

    # A run can be capped *and* stopped early; only claim full chunk
    # coverage when ``partial`` says the run reached every chunk and no
    # split chunk lost a half (its files went unreviewed).
    coverage = (
        ""
        if metadata.partial or lost_half or turn_limited or not_redone
        else "Every chunk was reviewed, but "
    )
    # A split chunk lost its whole-chunk view; a run degraded solely by an
    # incomplete optional pass says so instead.
    tail = (
        "findings that need the whole chunk in view may go unreported."
        if split_chunks
        or any(
            item.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
            and item.split
            and item.chunk_index == CARRIED_CHUNK_INDEX
            for item in recorded
        )
        else "some issues may go unreported."
    )
    if not coverage:
        tail = tail[0].upper() + tail[1:]
    return f"{'; '.join(clauses)}. {coverage}{tail}"


def format_narrative_note(*, metadata: ReviewMetadata) -> str:
    """Return the note for the narrative degradations no pass note explains.

    The synthesis and verification notes are built from those passes'
    outcomes. A round that carries an earlier round's failure of a pass it
    did not run again (#2803) has no outcome to describe, so the failure is
    named here; the failed per-PR question pass has no outcome note at all.

    Args:
        metadata: Review run metadata carrying ``coverage_degradations``.

    Returns:
        One sentence per such degradation, or an empty string when there is
        none. The text is identical whether the reason was recorded by this
        round or carried from the last one.
    """
    reasons = {item.reason for item in metadata.coverage_degradations}
    sentences = []
    if CoverageDegradationReason.GENERATED_QUESTIONS_FAILED in reasons:
        sentences.append(GENERATED_QUESTIONS_FAILED_NOTE)
    if metadata.synthesis is None and reasons & SYNTHESIS_DEGRADATION_REASONS:
        sentences.append(CARRIED_SYNTHESIS_NOTE)
    if (
        metadata.verification is None
        and CoverageDegradationReason.VERIFICATION_FAILED in reasons
    ):
        sentences.append(CARRIED_VERIFICATION_NOTE)
    return " ".join(sentences)
