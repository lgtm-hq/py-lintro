"""Shared wording for findings-cap / output-exhaustion coverage limits (#2003).

Every surface (terminal, GitHub review body, sticky comment) describes a
degraded run with the same sentence built here, so a capped review can never
read as complete on one surface and limited on another.
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
    "describe_coverage_degradations",
]

#: Short label reused as the bold lead-in on the posted GitHub surfaces.
COVERAGE_LIMITED_HEADLINE = "Coverage limited — not a guaranteed full finding set"


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
        A plain-text sentence naming the capped chunk counts, the caps in
        force, and any incomplete optional pass, or an empty string when the
        run was fully uncapped. The text carries no markup so the terminal and
        the GitHub surfaces can share it verbatim.
    """
    degradations = metadata.coverage_degradations
    if not degradations:
        return ""

    capped = [
        item
        for item in degradations
        if item.reason is CoverageDegradationReason.FINDINGS_CAP_APPLIED
    ]
    retried = [
        item
        for item in degradations
        if item.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
    ]
    # Rows are per limit event, not per chunk: a capped chunk that also
    # retried contributes two rows with one chunk_index. Count chunks by
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
    if capped:
        capped_chunks = len({item.chunk_index for item in capped})
        caps = sorted({item.findings_cap for item in capped})
        cap_text = "/".join(str(cap) for cap in caps)
        clauses.append(
            f"{capped_chunks} of {total} {_plural(count=total, noun='chunk')} "
            f"ran under a {cap_text}-finding per-call cap",
        )
    if retried:
        retried_chunks = len({item.chunk_index for item in retried})
        caps = sorted({item.findings_cap for item in retried})
        cap_text = "/".join(str(cap) for cap in caps)
        clauses.append(
            f"{retried_chunks} {_plural(count=retried_chunks, noun='chunk')} "
            f"retried at a tighter {cap_text}-finding cap after exhausting the "
            "provider output limit",
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
        CoverageDegradationReason.FINDINGS_CAP_APPLIED,
        CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        CoverageDegradationReason.SYNTHESIS_TRUNCATED,
        CoverageDegradationReason.SYNTHESIS_FAILED,
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
    # Only a real per-call ceiling can be blamed for lost low-severity depth;
    # a run degraded solely by an incomplete optional pass says so instead.
    tail = (
        "lower-severity issues beyond the cap may go unreported."
        if (capped or retried)
        else "some issues may go unreported."
    )
    if not coverage:
        tail = tail[0].upper() + tail[1:]
    return f"{'; '.join(clauses)}. {coverage}{tail}"
