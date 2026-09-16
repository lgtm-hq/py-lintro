"""The synthesis pass has its own input budget and records what it saw (#2704).

The chunker splits any diff above ``ai.review_chunk_diff_tokens``, so when the
synthesis pass was fitted to that same budget every multi-chunk PR was over it
by construction and every such round was recorded as ``synthesis_truncated``
and, through ``findings_coverage_complete``, reported as a partial review with
exit code 1. #2702 fixed the semantics (see ``test_synthesis_not_partial_2702``);
this file pins the budget the pass now gets and the record it writes.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.review.cli_limits import (
    REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
    resolve_synthesis_diff_budget,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.github_notes import format_synthesis_note_line
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.synthesis_prompt import plan_synthesis_prompt
from lintro.ai.token_budget import estimate_tokens

_DEFAULT_SYNTHESIS_BUDGET = int(
    AIConfig.model_fields["review_synthesis_diff_tokens"].default,
)


def _file_section(*, path: str, tokens: int) -> str:
    """Return one file's unified-diff section of roughly *tokens* tokens."""
    header = (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1,999 @@\n"
    )
    line = "+x = 'abcdefghijklmnopqrstuvwxyz0123456789' * 3\n"
    body = line * max((tokens * 4 - len(header)) // len(line), 1)
    return header + body


def _context(*, sections: list[str]) -> ReviewContext:
    return ReviewContext(
        base_ref="main",
        head_ref="HEAD",
        changed_files=[],
        unified_diff="".join(sections),
        pr_metadata=None,
    )


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
            diff_files_included=2,
            diff_files_total=5,
        ),
    )


# --- the budget ---------------------------------------------------------------


def test_three_default_chunks_fit_the_default_synthesis_budget_whole() -> None:
    """Three chunks at the 7,000 chunk budget fit the 24,000 synthesis budget."""
    sections = [
        _file_section(path=f"pkg/mod{i}.py", tokens=REVIEW_CHUNK_DIFF_TOKEN_BUDGET)
        for i in range(3)
    ]
    for section in sections:
        assert_that(estimate_tokens(section)).is_between(
            REVIEW_CHUNK_DIFF_TOKEN_BUDGET - 50,
            REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
        )
    summaries = [
        ChunkSummary(chunk_id=i, files=(f"pkg/mod{i}.py",), findings=())
        for i in range(3)
    ]

    plan = plan_synthesis_prompt(
        context=_context(sections=sections),
        summaries=summaries,
        diff_budget=_DEFAULT_SYNTHESIS_BUDGET,
    )

    assert_that(plan.truncated).is_false()
    assert_that(plan.diff_files_included).is_equal_to(3)
    assert_that(plan.diff_files_total).is_equal_to(3)
    # The same three chunks at the chunk budget were, by construction, over it.
    at_chunk_budget = plan_synthesis_prompt(
        context=_context(sections=sections),
        summaries=summaries,
        diff_budget=REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
    )
    assert_that(at_chunk_budget.truncated).is_true()
    assert_that(at_chunk_budget.diff_files_included).is_less_than(3)


def test_synthesis_budget_is_clamped_to_the_context_window_remainder() -> None:
    """The knob never exceeds what the context window leaves for the prompt."""
    assert_that(
        resolve_synthesis_diff_budget(
            context_window_budget=100_000,
            review_synthesis_diff_tokens=24_000,
        ),
    ).is_equal_to(24_000)
    assert_that(
        resolve_synthesis_diff_budget(
            context_window_budget=9_000,
            review_synthesis_diff_tokens=24_000,
        ),
    ).is_equal_to(9_000)
    assert_that(
        resolve_synthesis_diff_budget(
            context_window_budget=0,
            review_synthesis_diff_tokens=24_000,
        ),
    ).is_equal_to(1)


def test_synthesis_budget_knob_defaults_and_validates() -> None:
    """``ai.review_synthesis_diff_tokens`` defaults to 24,000 and rejects tiny values."""
    assert_that(AIConfig().review_synthesis_diff_tokens).is_equal_to(24_000)
    assert_that(AIConfig().budget_config.review_synthesis_diff_tokens).is_equal_to(
        24_000,
    )
    assert_that(
        AIConfig(review_synthesis_diff_tokens=8_000).review_synthesis_diff_tokens,
    ).is_equal_to(8_000)
    with pytest.raises(ValidationError):
        AIConfig(review_synthesis_diff_tokens=500)


# --- the note -------------------------------------------------------------------


def test_the_synthesis_note_names_the_files_seen_and_the_merge_caveat() -> None:
    """The sticky says what a cut synthesis input can actually miss (#2269)."""
    note = format_synthesis_note_line(
        metadata=_metadata(CoverageDegradationReason.SYNTHESIS_TRUNCATED),
    )
    assert_that(note).contains("saw 2 of 5 changed files")
    assert_that(note).contains("cross-chunk duplicate merging may be incomplete")
    failed = format_synthesis_note_line(
        metadata=_metadata(CoverageDegradationReason.SYNTHESIS_FAILED),
    )
    assert_that(failed).contains("did not complete")
    assert_that(failed).contains("duplicate merging was not applied")


def test_a_note_without_file_counts_falls_back_to_the_generic_wording() -> None:
    """A record from before the counts existed still renders a sentence."""
    metadata = ReviewMetadata(
        model="m",
        provider="anthropic",
        context_window=1,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        synthesis=SynthesisOutcome(truncated=True),
    )
    note = format_synthesis_note_line(metadata=metadata)
    assert_that(note).contains("less than its whole input")
    assert_that(note).contains("duplicate merging may be incomplete")


# --- the record -----------------------------------------------------------------


def test_the_synthesis_record_carries_its_budget_and_sizes() -> None:
    """The JSON block names the call's tokens, limit, budget and file counts."""
    outcome = SynthesisOutcome(
        findings_added=1,
        truncated=True,
        input_tokens=21_000,
        output_tokens=3_200,
        output_limit_tokens=None,
        input_budget_tokens=24_000,
        prompt_tokens_estimated=23_900,
        diff_files_included=6,
        diff_files_total=8,
    )
    block = outcome.to_dict()
    assert_that(block).contains_entry(
        {"input_tokens": 21_000},
        {"output_tokens": 3_200},
        {"output_limit_tokens": None},
        {"input_budget_tokens": 24_000},
        {"prompt_tokens_estimated": 23_900},
        {"diff_files_included": 6},
        {"diff_files_total": 8},
    )
    # narrative_missing is untouched by the new fields (GO amendment 4).
    assert_that(block["narrative_missing"]).is_false()
    assert_that(
        SynthesisOutcome(truncated=True, narrative_missing=True).to_dict()[
            "narrative_missing"
        ],
    ).is_true()


# --- the persisted record -------------------------------------------------------


def test_the_run_record_keeps_a_synthesis_degraded_round_visible() -> None:
    """History shows the round as synthesis-limited without calling it capped."""
    from lintro.ai.review.models.run_coverage import RunCoverage
    from lintro.ai.review.models.run_identity import RunIdentity
    from lintro.ai.review.models.run_record import RunRecord

    degraded = RunRecord(
        identity=RunIdentity(round=1, sha="abc1234"),
        coverage=RunCoverage(synthesis_degraded=True),
    )
    payload = degraded.to_dict()
    assert_that(payload["synthesis_degraded"]).is_true()
    assert_that(payload).does_not_contain_key("coverage_limited")
    restored = RunRecord.from_dict(payload)
    assert_that(restored.coverage.synthesis_degraded).is_true()
    assert_that(restored.coverage.coverage_limited).is_false()
    # A record from before the field existed re-encodes without it.
    plain = RunRecord(identity=RunIdentity(round=1, sha="abc1234")).to_dict()
    assert_that(plain).does_not_contain_key("synthesis_degraded")


def test_the_factory_records_synthesis_degraded_from_the_result() -> None:
    """``run_record_from_result`` copies ``ReviewMetadata.synthesis_degraded``."""
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.review.run_record_factory import _coverage

    metadata = _metadata(CoverageDegradationReason.SYNTHESIS_TRUNCATED)
    result = ReviewResult(findings=(), metadata=metadata, summary="")
    coverage = _coverage(result=result)
    assert_that(coverage.synthesis_degraded).is_true()
    assert_that(coverage.coverage_limited).is_false()
