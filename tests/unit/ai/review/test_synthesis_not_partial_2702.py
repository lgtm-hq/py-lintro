"""A truncated or failed synthesis pass never makes a review partial (#2702).

Findings coverage is per file. The whole-PR synthesis pass adds the narrative,
duplicate merges and cross-file findings on top; when it saw part of the diff
or did not complete, that is a narrative degradation carried by the synthesis
note (#2269), not a limit on per-file finding depth, so it must not set the
"Partial review" label or fail the CI check through ``findings_coverage_complete``.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.coverage_degradation import describe_coverage_degradations
from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.github_notes import (
    format_coverage_limited_warning,
    format_partial_review_label,
    format_synthesis_note_line,
)
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome


def _metadata(*reasons: CoverageDegradationReason) -> ReviewMetadata:
    return ReviewMetadata(
        model="m",
        provider="anthropic",
        context_window=1,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=tuple(
            CoverageDegradation(
                reason=reason,
                chunk_index=(
                    SYNTHESIS_CHUNK_INDEX
                    if reason in NARRATIVE_DEGRADATION_REASONS
                    else 0
                ),
            )
            for reason in reasons
        ),
        synthesis=SynthesisOutcome(
            truncated=CoverageDegradationReason.SYNTHESIS_TRUNCATED in reasons,
            failed=CoverageDegradationReason.SYNTHESIS_FAILED in reasons,
        ),
    )


def test_a_synthesis_degradation_alone_keeps_findings_coverage_complete() -> None:
    """Truncated or failed synthesis is not a partial review on any surface."""
    for reason in (
        CoverageDegradationReason.SYNTHESIS_TRUNCATED,
        CoverageDegradationReason.SYNTHESIS_FAILED,
    ):
        metadata = _metadata(reason)
        assert_that(metadata.findings_coverage_complete).described_as(
            str(reason),
        ).is_true()
        assert_that(metadata.synthesis_degraded).is_true()
        assert_that(format_partial_review_label(metadata=metadata)).is_empty()
        assert_that(format_coverage_limited_warning(metadata=metadata)).is_empty()
        assert_that(describe_coverage_degradations(metadata=metadata)).is_empty()


def test_a_per_file_degradation_still_makes_the_review_partial() -> None:
    """The per-file reasons keep their meaning alongside a synthesis reason."""
    metadata = _metadata(
        CoverageDegradationReason.SYNTHESIS_TRUNCATED,
        CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
    )
    assert_that(metadata.findings_coverage_complete).is_false()
    assert_that(metadata.synthesis_degraded).is_true()
    assert_that(format_partial_review_label(metadata=metadata)).is_not_empty()
    warning = format_coverage_limited_warning(metadata=metadata)
    assert_that(warning).contains("output")
    assert_that(warning).does_not_contain("synthesis")


def test_the_synthesis_note_carries_the_merge_caveat() -> None:
    """The note says what a cut or failed synthesis can actually miss (#2269)."""
    note = format_synthesis_note_line(
        metadata=_metadata(CoverageDegradationReason.SYNTHESIS_TRUNCATED),
    )
    assert_that(note).contains("less than its whole input")
    assert_that(note).contains("cross-chunk duplicate merging may be incomplete")
    failed = format_synthesis_note_line(
        metadata=_metadata(CoverageDegradationReason.SYNTHESIS_FAILED),
    )
    assert_that(failed).contains("did not complete")
    assert_that(failed).contains("duplicate merging was not applied")
