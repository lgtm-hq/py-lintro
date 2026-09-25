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
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import CARRIED_CHUNK_INDEX

if TYPE_CHECKING:
    from lintro.ai.review.models.review_metadata import ReviewMetadata

__all__ = [
    "COVERAGE_LIMITED_HEADLINE",
    "PARTIAL_REVIEW_LABEL",
    "GENERATED_QUESTIONS_FAILED_NOTE",
    "describe_coverage_degradations",
    "format_question_pass_note",
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
            str(item.reason)
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
            "work the previous attempt at this head degraded was not redone "
            f"({', '.join(not_redone)}); the next round redoes it",
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
        else "some issues may go unreported."
    )
    if not coverage:
        tail = tail[0].upper() + tail[1:]
    return f"{'; '.join(clauses)}. {coverage}{tail}"


def format_question_pass_note(*, metadata: ReviewMetadata) -> str:
    """Return the note for a run whose per-PR question pass failed.

    Args:
        metadata: Review run metadata carrying ``coverage_degradations``.

    Returns:
        :data:`GENERATED_QUESTIONS_FAILED_NOTE`, or an empty string when the pass
        did not fail. A rerun that carried the failure forward (#2803)
        records the same reason, so it renders the same note.
    """
    if any(
        item.reason is CoverageDegradationReason.GENERATED_QUESTIONS_FAILED
        for item in metadata.coverage_degradations
    ):
        return GENERATED_QUESTIONS_FAILED_NOTE
    return ""
