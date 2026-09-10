"""Goldens for the chunk merge stage (issue #2298).

`merge_review_results` and its helpers decide finding order, dedup, checklist
precedence, summary joining and per-file assessment ownership. Only
`merge_findings` and `merge_checklist_answers` had unit coverage before this
suite; `merge_verdict_reasoning` and `merge_file_assessments` had none.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.providers.response import AIResponse
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    merge_checklist_answers,
    merge_file_assessments,
    merge_findings,
    merge_pr_summaries,
    merge_review_results,
    merge_verdict_reasoning,
)
from lintro.ai.review.response_pipeline import payload_to_partial
from tests.unit.ai.review.golden.golden_fixtures import GOLDEN_RESPONSES
from tests.unit.ai.review.golden.golden_io import (
    assert_golden_json,
    load_payload,
)


def _golden_partials() -> list[ChunkReviewPartial]:
    """Build chunk partials from the fixed provider response payloads.

    Returns:
        One partial per payload file, in chunk order.
    """
    partials: list[ChunkReviewPartial] = []
    for name, input_tokens, output_tokens, cost in GOLDEN_RESPONSES:
        response = AIResponse(
            content="",
            model="golden-model",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
            provider="golden-provider",
        )
        partials.append(
            payload_to_partial(response=response, payload=load_payload(name=name)),
        )
    return partials


def test_chunk_partials_match_golden() -> None:
    """Payload-to-partial parsing (including the P1 evidence gate) is pinned."""
    assert_golden_json(name="chunk_partials.golden", value=_golden_partials())


def test_merge_review_results_matches_golden() -> None:
    """The merged review result shell is byte-stable across the decomposition.

    Metadata is the placeholder ``merge_review_results`` attaches; the real
    run metadata is pinned by ``test_run_review_goldens``.
    """
    merged = merge_review_results(partials=_golden_partials())

    assert_golden_json(name="merged_review_result.golden", value=merged)


def test_merge_helper_outputs_match_goldens() -> None:
    """Each merge helper's own output is pinned, not only the composite."""
    partials = _golden_partials()

    assert_golden_json(
        name="merge_findings.golden",
        value=merge_findings(
            findings_groups=[partial.findings for partial in partials],
        ),
    )
    assert_golden_json(
        name="merge_checklist_answers.golden",
        value=merge_checklist_answers(
            checklist_groups=[partial.checklist for partial in partials],
        ),
    )
    assert_golden_json(
        name="merge_pr_summaries.golden",
        value=merge_pr_summaries(partials=partials),
    )
    assert_golden_json(
        name="merge_verdict_reasoning.golden",
        value=merge_verdict_reasoning(partials=partials),
    )
    assert_golden_json(
        name="merge_file_assessments.golden",
        value=merge_file_assessments(partials=partials),
    )


def test_merge_of_no_partials_matches_golden() -> None:
    """The empty-partials shell is a documented shape, so it is pinned too."""
    assert_golden_json(
        name="merged_review_result_empty.golden",
        value=merge_review_results(partials=[]),
    )


def test_merged_findings_are_deduplicated_by_location_and_title() -> None:
    """The cross-chunk duplicate collapses to one finding, first chunk winning.

    Stated as an assertion as well as a golden so a regression names the rule
    it broke instead of only reporting a file diff.
    """
    merged = merge_review_results(partials=_golden_partials())
    keys = [(finding.file, finding.line, finding.title) for finding in merged.findings]

    assert_that(keys).is_equal_to(sorted(set(keys), key=keys.index))
    assert_that(keys).is_length(3)
