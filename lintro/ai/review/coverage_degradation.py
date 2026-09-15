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
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import SYNTHESIS_CHUNK_INDEX

if TYPE_CHECKING:
    from lintro.ai.review.models.review_metadata import ReviewMetadata

__all__ = [
    "COVERAGE_LIMITED_HEADLINE",
    "PARTIAL_REVIEW_LABEL",
    "describe_coverage_degradations",
]

#: Short label reused as the bold lead-in on the posted GitHub surfaces.
COVERAGE_LIMITED_HEADLINE = "Coverage limited — not a guaranteed full finding set"

#: What a degraded run is called in the *header* of each posted surface
#: (#2395). The warning below it explains why; the header exists so a reader
#: who never scrolls past the first line still learns the review is partial,
#: and so it matches the ``degraded`` outcome the CI check reports.
PARTIAL_REVIEW_LABEL = "Partial review"

#: How each depth >= 2 pass failure is named in the sentence (#2395). These
#: are per-chunk reasons that carry no per-call ceiling, so they get their own
#: clause rather than joining the cap wording.
_DEPTH_PASS_CLAUSES: dict[CoverageDegradationReason, str] = {
    CoverageDegradationReason.GENERATED_QUESTIONS_FAILED: (
        "the depth-2 generated-questions pass failed"
    ),
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
    degradations = metadata.coverage_degradations
    if not degradations:
        return ""

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
    affected = {
        item.chunk_index
        for item in degradations
        if item.chunk_index != SYNTHESIS_CHUNK_INDEX
    }
    total = max(metadata.chunks_total, len(affected))

    clauses: list[str] = []
    if retried:
        retried_chunks = len({item.chunk_index for item in retried})
        clauses.append(
            f"{retried_chunks} of {total} {_plural(count=total, noun='chunk')} "
            "exhausted the provider output limit and "
            f"{'was' if retried_chunks == 1 else 'were'} split and re-reviewed "
            "in halves",
        )

    for reason, wording in _DEPTH_PASS_CLAUSES.items():
        chunks = {item.chunk_index for item in degradations if item.reason is reason}
        if chunks:
            clauses.append(
                f"{len(chunks)} {_plural(count=len(chunks), noun='chunk')} kept "
                f"only the main pass after {wording}",
            )

    reasons = {item.reason for item in degradations}
    if CoverageDegradationReason.SYNTHESIS_TRUNCATED in reasons:
        clauses.append(
            "the cross-chunk synthesis pass saw less than its whole input "
            "(whole-PR token budget)",
        )
    if CoverageDegradationReason.SYNTHESIS_FAILED in reasons:
        clauses.append("the cross-chunk synthesis pass did not complete")

    known = {
        CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        CoverageDegradationReason.SYNTHESIS_TRUNCATED,
        CoverageDegradationReason.SYNTHESIS_FAILED,
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
    # coverage when ``partial`` says the run reached every chunk.
    coverage = "" if metadata.partial else "Every chunk was reviewed, but "
    # A split chunk lost its whole-chunk view; a run degraded solely by an
    # incomplete optional pass says so instead.
    tail = (
        "findings that need the whole chunk in view may go unreported."
        if retried
        else "some issues may go unreported."
    )
    if not coverage:
        tail = tail[0].upper() + tail[1:]
    return f"{'; '.join(clauses)}. {coverage}{tail}"
