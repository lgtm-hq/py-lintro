"""Findings-cap / output-exhaustion coverage signals (issue #2003).

A CLI review that ran under ``ai.cli_max_findings_per_call``, or that retried a
chunk at a tighter cap after exhausting the provider output ceiling, reviewed
every chunk but was told to stop at N findings. These tests pin that such a run
is distinguishable from an uncapped one in metadata, on the terminal, on both
posted GitHub surfaces, and in the JSON and MCP payloads — and that an uncapped
run still renders byte-identically to before the signal existed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.cli_limits import (
    CLI_FINDINGS_RETRY_CAP,
    CLI_MAX_FINDINGS_PER_CALL,
)
from lintro.ai.review.coverage_degradation import (
    COVERAGE_LIMITED_HEADLINE,
    describe_coverage_degradations,
)
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.github_sticky import build_sticky_comment
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.orchestrator import (
    # Deliberate private import: the retry loop is unit-tested at the helper
    # seam, matching the #1967 tests that already pin this function.
    _invoke_chunk_review,
    run_review_async,
)
from lintro.ai.review.output import review_result_to_dict
from lintro.mcp.toolkits.review import _run_metadata

_CAP = CoverageDegradation(
    reason=CoverageDegradationReason.FINDINGS_CAP_APPLIED,
    chunk_index=0,
    findings_cap=12,
)
_RETRY = CoverageDegradation(
    reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
    chunk_index=1,
    findings_cap=6,
)


def _with_degradations(
    *,
    result: ReviewResult,
    degradations: tuple[CoverageDegradation, ...],
) -> ReviewResult:
    """Return ``result`` with its metadata carrying ``degradations``.

    Args:
        result: Base review result.
        degradations: Coverage degradations to stamp on the metadata.

    Returns:
        A copy of the result whose metadata records the degradations.
    """
    return replace(
        result,
        metadata=replace(result.metadata, coverage_degradations=degradations),
    )


def _body(*, result: ReviewResult) -> str:
    """Render the per-review GitHub body through the public builder.

    Args:
        result: Review result to render.

    Returns:
        The rendered Markdown body.
    """
    prior_state = ReviewState()
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha="fb740b2",
    )
    return build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha="fb740b2",
        transport="cli",
        auth_mode="subscription",
    )


def _sticky(*, result: ReviewResult) -> str:
    """Render the sticky comment through the public builder.

    Args:
        result: Review result to render.

    Returns:
        The rendered sticky comment body.
    """
    return build_sticky_comment(
        result=result,
        transport="cli",
        auth_mode="subscription",
    )


def _terminal(*, result: ReviewResult) -> str:
    """Render the terminal review output to a string.

    Args:
        result: Review result to render.

    Returns:
        The captured terminal text.
    """
    console = Console(width=200, force_terminal=False, no_color=True)
    with console.capture() as capture:
        render_review_terminal(result=result, console=console)
    return capture.get()


# --- metadata schema ---------------------------------------------------------


def test_uncapped_run_reports_complete_coverage(
    sample_review_result: ReviewResult,
) -> None:
    """A run with no recorded degradation is coverage-complete.

    Args:
        sample_review_result: Shared review result fixture.
    """
    metadata = sample_review_result.metadata

    assert_that(metadata.coverage_degradations).is_empty()
    assert_that(metadata.findings_coverage_complete).is_true()
    assert_that(metadata.findings_cap_applied).is_none()
    assert_that(metadata.output_exhaustion_retried).is_false()


def test_findings_cap_is_recorded_without_flipping_partial(
    sample_review_result: ReviewResult,
) -> None:
    """A capped run is coverage-limited but not ``partial``.

    ``partial`` means chunks went unreviewed; a findings cap reviewed every
    chunk at reduced depth, so the two axes stay independent.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=(_CAP,),
    )

    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(result.metadata.findings_cap_applied).is_equal_to(12)
    assert_that(result.metadata.output_exhaustion_retried).is_false()
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.stopped_reason).is_equal_to("")


def test_exhaustion_retry_reports_the_tightened_cap(
    sample_review_result: ReviewResult,
) -> None:
    """The retry records the tighter ceiling it actually ran under.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=(_CAP, _RETRY),
    )

    assert_that(result.metadata.output_exhaustion_retried).is_true()
    assert_that(result.metadata.findings_cap_applied).is_equal_to(6)


# --- shared wording ----------------------------------------------------------


def test_description_is_empty_for_a_complete_run(
    sample_review_result: ReviewResult,
) -> None:
    """An uncapped run produces no coverage sentence at all.

    Args:
        sample_review_result: Shared review result fixture.
    """
    described = describe_coverage_degradations(
        metadata=sample_review_result.metadata,
    )

    assert_that(described).is_empty()


@pytest.mark.parametrize(
    ("degradations", "expected"),
    [
        ((_CAP,), "12-finding per-call cap"),
        ((_CAP, _RETRY), "tighter 6-finding cap"),
    ],
    ids=["case=cap_only", "case=cap_and_retry"],
)
def test_description_names_the_cap_in_force(
    sample_review_result: ReviewResult,
    degradations: tuple[CoverageDegradation, ...],
    expected: str,
) -> None:
    """The shared sentence names the ceiling and warns findings may be missing.

    Args:
        sample_review_result: Shared review result fixture.
        degradations: Degradations stamped on the metadata.
        expected: Substring the sentence must carry for this case.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=degradations,
    )

    described = describe_coverage_degradations(metadata=result.metadata)

    assert_that(described).contains(expected)
    assert_that(described).contains("may go unreported")


# --- surfaces ----------------------------------------------------------------


def test_terminal_banner_only_appears_for_a_capped_run(
    sample_review_result: ReviewResult,
) -> None:
    """The terminal warns on a capped run and is unchanged on a clean one.

    Args:
        sample_review_result: Shared review result fixture.
    """
    clean = _terminal(result=sample_review_result)
    capped = _terminal(
        result=_with_degradations(
            result=sample_review_result,
            degradations=(_CAP, _RETRY),
        ),
    )

    assert_that(clean).does_not_contain(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains("12-finding per-call cap")


def test_review_body_carries_the_warning_only_when_capped(
    sample_review_result: ReviewResult,
) -> None:
    """The posted review body warns on a capped run, byte-identical otherwise.

    Args:
        sample_review_result: Shared review result fixture.
    """
    clean = _body(result=sample_review_result)
    capped = _body(
        result=_with_degradations(
            result=sample_review_result,
            degradations=(_CAP,),
        ),
    )

    assert_that(clean).does_not_contain(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains(f"> ⚠️ **{COVERAGE_LIMITED_HEADLINE}**")
    # Production-independent copy: the detail sentence, not just the headline.
    assert_that(clean).does_not_contain("may go unreported")
    assert_that(capped).contains("ran under a 12-finding per-call cap")
    assert_that(capped).contains("may go unreported")


def test_sticky_carries_the_warning_only_when_capped(
    sample_review_result: ReviewResult,
) -> None:
    """The sticky comment marks a capped round, byte-identical otherwise.

    Args:
        sample_review_result: Shared review result fixture.
    """
    clean = _sticky(result=sample_review_result)
    capped = _sticky(
        result=_with_degradations(
            result=sample_review_result,
            degradations=(_RETRY,),
        ),
    )

    assert_that(clean).does_not_contain(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains(f"> ⚠️ **{COVERAGE_LIMITED_HEADLINE}**")
    # Production-independent copy: the detail sentence, not just the headline.
    assert_that(clean).does_not_contain("may go unreported")
    assert_that(capped).contains("retried at a tighter 6-finding cap")
    assert_that(capped).contains("may go unreported")


def test_uncapped_run_renders_identically_on_every_surface(
    sample_review_result: ReviewResult,
) -> None:
    """An explicitly-empty degradation tuple changes no rendered surface.

    Args:
        sample_review_result: Shared review result fixture.
    """
    baseline = sample_review_result
    explicit = _with_degradations(result=baseline, degradations=())

    assert_that(_terminal(result=explicit)).is_equal_to(_terminal(result=baseline))
    assert_that(_body(result=explicit)).is_equal_to(_body(result=baseline))
    assert_that(_sticky(result=explicit)).is_equal_to(_sticky(result=baseline))


# --- history ------------------------------------------------------------------


def test_run_record_round_trips_the_coverage_flag() -> None:
    """A coverage-limited round persists and parses back as limited."""
    record = RunRecord(round=1, coverage_limited=True)

    payload = record.to_dict()

    assert_that(payload).contains_key("coverage_limited")
    assert_that(RunRecord.from_dict(payload).coverage_limited).is_true()


def test_run_record_omits_the_flag_for_a_complete_round() -> None:
    """A legacy or complete record keeps its byte-identical serialized shape."""
    payload = RunRecord(round=1).to_dict()

    assert_that(payload).does_not_contain_key("coverage_limited")
    assert_that(RunRecord.from_dict(payload).coverage_limited).is_false()


# --- machine-readable payloads ------------------------------------------------


def test_json_payload_exposes_the_coverage_fields(
    sample_review_result: ReviewResult,
) -> None:
    """``--output json`` carries the signals a classifier needs.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=(_CAP, _RETRY),
    )

    payload = review_result_to_dict(result=result)

    assert_that(payload["findings_coverage_complete"]).is_false()
    assert_that(payload["findings_cap_applied"]).is_equal_to(6)
    assert_that(payload["output_exhaustion_retried"]).is_true()
    assert_that(payload["coverage_degradations"]).is_equal_to(
        [
            {
                "reason": "findings_cap_applied",
                "chunk_index": 0,
                "findings_cap": 12,
            },
            {
                "reason": "output_exhaustion_retried",
                "chunk_index": 1,
                "findings_cap": 6,
            },
        ],
    )
    # The reason must survive JSON encoding as a plain string, not an enum repr.
    assert_that(json.loads(json.dumps(payload))["coverage_degradations"]).is_equal_to(
        payload["coverage_degradations"],
    )


def test_json_payload_marks_an_uncapped_run_complete(
    sample_review_result: ReviewResult,
) -> None:
    """A clean run states completeness rather than omitting the key.

    Args:
        sample_review_result: Shared review result fixture.
    """
    payload = review_result_to_dict(result=sample_review_result)

    assert_that(payload["findings_coverage_complete"]).is_true()
    assert_that(payload["coverage_degradations"]).is_empty()
    assert_that(payload["findings_cap_applied"]).is_none()
    assert_that(payload["output_exhaustion_retried"]).is_false()


def test_mcp_run_block_exposes_the_coverage_fields(
    sample_review_result: ReviewResult,
) -> None:
    """The MCP ``run`` block reports the same signals as the JSON payload.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=(_CAP,),
    )

    run = _run_metadata(metadata=result.metadata)

    assert_that(run["findings_coverage_complete"]).is_false()
    assert_that(run["findings_cap_applied"]).is_equal_to(12)
    assert_that(run["output_exhaustion_retried"]).is_false()
    assert_that(run["coverage_degradations"]).is_length(1)
    # A capped run is not the same condition as an early stop.
    assert_that(run["partial"]).is_false()


# --- orchestrator recording ---------------------------------------------------


def _chunk_and_context(*, repo_root: str) -> tuple[ReviewChunk, ReviewContext]:
    """Build a one-file chunk and its review context.

    Args:
        repo_root: Absolute path used as the review's repository root.

    Returns:
        The chunk and the context that carries its diff.
    """
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py"],
        diff="+x = 1\n",
        relationship="single-file",
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="src/a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=chunk.diff,
        pr_metadata=None,
        repo_root=repo_root,
    )
    return chunk, context


def _ok_response() -> AIResponse:
    """Return a minimal well-formed chunk review response.

    Returns:
        A parseable provider response with no findings.
    """
    payload = {
        "summary": {"headline": "Adds a constant.", "walkthrough": []},
        "checklist": [],
        "findings": [],
        "verdict_reasoning": {
            "deciding_factor": "Nothing blocks.",
            "failure_mechanism": "n/a",
            "files_needing_attention": [],
        },
        "file_assessments": [],
    }
    return AIResponse(
        content=json.dumps(payload),
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.0,
    )


async def _degradations_for(
    *,
    tmp_path: Path,
    max_findings: int | None,
    exhaust_first_call: bool,
) -> tuple[CoverageDegradation, ...]:
    """Drive the chunk-review seam and return what it recorded.

    Args:
        tmp_path: Temporary directory used as the repository root.
        max_findings: Per-call findings ceiling handed to the chunk call.
        exhaust_first_call: When True, the first provider call fails with an
            output-token exhaustion error so the tighter-cap retry runs.

    Returns:
        The coverage degradations the chunk call recorded.
    """
    chunk, context = _chunk_and_context(repo_root=str(tmp_path))
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    budget = MagicMock()
    budget.check = MagicMock()
    calls: list[int] = []

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        """Fail the first call on output exhaustion when asked to."""
        calls.append(1)
        if exhaust_first_call and len(calls) == 1:
            raise AIProviderError(
                "Claude CLI reported error: maximum output tokens reached",
            )
        return _ok_response()

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        _response, _elapsed, degradations = await _invoke_chunk_review(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                review=True,
                transport=AITransport.CLI,
            ),
            checklist_text="",
            checklist_count=0,
            interaction_paths="",
            lint_results=None,
            extra_checklist="",
            strictness_section="",
            budget=budget,
            repo_root=str(tmp_path),
            use_one_shot=True,
            diff_budget=10_000,
            max_findings=max_findings,
            chunk_index=3,
        )
    return degradations


async def test_chunk_review_records_the_applied_findings_cap(
    tmp_path: Path,
) -> None:
    """A capped chunk call records the cap it was given.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    degradations = await _degradations_for(
        tmp_path=tmp_path,
        max_findings=CLI_MAX_FINDINGS_PER_CALL,
        exhaust_first_call=False,
    )

    assert_that(degradations).is_length(1)
    assert_that(degradations[0].reason).is_equal_to(
        CoverageDegradationReason.FINDINGS_CAP_APPLIED,
    )
    assert_that(degradations[0].findings_cap).is_equal_to(CLI_MAX_FINDINGS_PER_CALL)
    assert_that(degradations[0].chunk_index).is_equal_to(3)


async def test_chunk_review_records_the_exhaustion_retry(
    tmp_path: Path,
) -> None:
    """An output-exhaustion retry records the tightened cap it re-ran under.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    degradations = await _degradations_for(
        tmp_path=tmp_path,
        max_findings=CLI_MAX_FINDINGS_PER_CALL,
        exhaust_first_call=True,
    )

    reasons = [item.reason for item in degradations]
    assert_that(reasons).is_equal_to(
        [
            CoverageDegradationReason.FINDINGS_CAP_APPLIED,
            CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        ],
    )
    assert_that(degradations[-1].findings_cap).is_equal_to(CLI_FINDINGS_RETRY_CAP)


async def test_uncapped_chunk_review_records_nothing(
    tmp_path: Path,
) -> None:
    """A transport with no findings ceiling records no degradation.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    degradations = await _degradations_for(
        tmp_path=tmp_path,
        max_findings=None,
        exhaust_first_call=False,
    )

    assert_that(degradations).is_empty()


async def test_cli_run_metadata_carries_the_cap_end_to_end(
    tmp_path: Path,
) -> None:
    """A full CLI run surfaces the cap on the result metadata.

    Locks the orchestrator wiring: if the per-chunk degradations stop being
    aggregated onto ``ReviewMetadata``, a capped CLI review would present as
    an unlimited one again.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _chunk, context = _chunk_and_context(repo_root=str(tmp_path))
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        new=AsyncMock(return_value=_ok_response()),
    ):
        result = await run_review_async(
            context=context,
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                review=True,
                transport=AITransport.CLI,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
        )

    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(result.metadata.findings_cap_applied).is_equal_to(
        CLI_MAX_FINDINGS_PER_CALL,
    )
    assert_that(result.metadata.partial).is_false()


def test_partial_and_capped_run_does_not_claim_every_chunk_reviewed() -> None:
    """A run that is both capped and stopped early never over-claims coverage."""
    from lintro.ai.review.coverage_degradation import describe_coverage_degradations
    from lintro.ai.review.enums.coverage_degradation_reason import (
        CoverageDegradationReason,
    )
    from lintro.ai.review.models.coverage_degradation import CoverageDegradation
    from lintro.ai.review.models.review_metadata import ReviewMetadata

    capped = CoverageDegradation(
        reason=CoverageDegradationReason.FINDINGS_CAP_APPLIED,
        chunk_index=0,
        findings_cap=25,
    )
    complete = ReviewMetadata(
        model="m",
        provider="p",
        context_window=1,
        depth=1,
        chunks_total=2,
        chunks_current=2,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=(capped,),
    )
    partial = replace(complete, partial=True, chunks_reviewed=1)

    assert_that(describe_coverage_degradations(metadata=complete)).contains(
        "Every chunk was reviewed",
    )
    text = describe_coverage_degradations(metadata=partial)
    assert_that(text).does_not_contain("Every chunk was reviewed")
    assert_that(text).contains("Lower-severity issues beyond the cap")


def test_run_record_coverage_limited_uses_strict_bool_parsing() -> None:
    """A string ``"false"`` in a legacy blob must not read as limited."""
    from lintro.ai.review.models.run_record import RunRecord

    base = RunRecord().to_dict()

    assert_that(
        RunRecord.from_dict({**base, "coverage_limited": "false"}).coverage_limited,
    ).is_false()
    assert_that(
        RunRecord.from_dict({**base, "coverage_limited": True}).coverage_limited,
    ).is_true()
    assert_that(RunRecord.from_dict(base).coverage_limited).is_false()


def test_capped_and_retried_chunk_counts_once_in_the_description() -> None:
    """Two limit events on one chunk never inflate the chunk denominator."""
    from lintro.ai.review.coverage_degradation import describe_coverage_degradations
    from lintro.ai.review.enums.coverage_degradation_reason import (
        CoverageDegradationReason,
    )
    from lintro.ai.review.models.coverage_degradation import CoverageDegradation
    from lintro.ai.review.models.review_metadata import ReviewMetadata

    metadata = ReviewMetadata(
        model="m",
        provider="p",
        context_window=1,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.FINDINGS_CAP_APPLIED,
                chunk_index=0,
                findings_cap=25,
            ),
            CoverageDegradation(
                reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
                chunk_index=0,
                findings_cap=12,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("1 of 1 chunk ran under a 25-finding per-call cap")
    assert_that(text).contains("1 chunk retried at a tighter 12-finding cap")
    assert_that(text).does_not_contain("of 2 chunks")


def test_run_record_partial_uses_strict_bool_parsing() -> None:
    """The legacy ``partial`` flag gets the same string-safe parsing."""
    from lintro.ai.review.models.run_record import RunRecord

    base = RunRecord().to_dict()

    assert_that(RunRecord.from_dict({**base, "partial": "false"}).partial).is_false()
    assert_that(RunRecord.from_dict({**base, "partial": True}).partial).is_true()


def test_sticky_history_marks_a_prior_capped_round(
    sample_review_result: ReviewResult,
) -> None:
    """The run-history recap keeps a capped round visible in later rounds.

    Args:
        sample_review_result: Shared review result fixture.
    """
    from lintro.ai.review.github_sticky import build_sticky_bodies
    from lintro.ai.review.models.run_record import RunRecord

    limited = RunRecord(round=1, sha="abc1234", coverage_limited=True).to_dict()
    unlimited = RunRecord(round=1, sha="abc1234").to_dict()

    # The primary sticky archives run history into its companion body, so the
    # marker is asserted across both bodies the public builder returns.
    with_marker = "\n".join(
        body or ""
        for body in build_sticky_bodies(
            result=sample_review_result,
            prior_runs=[limited],
            transport="cli",
        )
    )
    without_marker = "\n".join(
        body or ""
        for body in build_sticky_bodies(
            result=sample_review_result,
            prior_runs=[unlimited],
            transport="cli",
        )
    )

    assert_that(with_marker).contains("Run-by-run history")
    assert_that(with_marker).contains("⚠️ coverage limited")
    assert_that(without_marker).does_not_contain("⚠️ coverage limited")


def test_advanced_state_persists_coverage_limited_from_a_capped_result(
    sample_review_result: ReviewResult,
) -> None:
    """A capped result stamps coverage_limited on the persisted run record.

    Args:
        sample_review_result: Shared review result fixture.
    """
    from lintro.ai.review.github_sticky import advance_review_state
    from lintro.ai.review.models.run_record import RunRecord

    capped_state = advance_review_state(
        result=_with_degradations(result=sample_review_result, degradations=(_CAP,)),
        head_sha="abc1234",
        transport="cli",
    )
    clean_state = advance_review_state(
        result=sample_review_result,
        head_sha="abc1234",
        transport="cli",
    )

    capped_run = capped_state.runs[-1]
    assert_that(capped_run.coverage_limited).is_true()
    assert_that(clean_state.runs[-1].coverage_limited).is_false()
    # The flag survives the flat persisted shape.
    assert_that(RunRecord.from_dict(capped_run.to_dict()).coverage_limited).is_true()


def test_unknown_degradation_reason_still_renders_a_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reason the describer does not know never yields an empty sentence.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.ai.review.coverage_degradation import describe_coverage_degradations
    from lintro.ai.review.models.review_metadata import ReviewMetadata

    class _Novel(str):
        """Stand-in for a future ``CoverageDegradationReason`` member."""

        def __str__(self) -> str:
            return "novel_limit"

    novel = replace(_CAP, reason=_Novel())  # type: ignore[arg-type]
    metadata = ReviewMetadata(
        model="m",
        provider="p",
        context_window=1,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=(novel,),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).starts_with("1 other limit applied (novel_limit).")
    assert_that(text[0]).is_not_equal_to(".")


@pytest.mark.parametrize(
    ("reason", "clause"),
    [
        (
            CoverageDegradationReason.SYNTHESIS_TRUNCATED,
            "saw less than its whole input",
        ),
        (
            CoverageDegradationReason.SYNTHESIS_FAILED,
            "did not complete",
        ),
    ],
)
def test_synthesis_degradation_is_never_counted_as_a_chunk(
    reason: CoverageDegradationReason,
    clause: str,
) -> None:
    """The whole-run sentinel stays out of the "X of Y chunks" denominator.

    Both whole-run reasons are pinned: exclusion keys on the sentinel chunk
    index, so a reason the aggregators forgot to special-case would inflate
    the denominator or win the ``findings_cap_applied`` min().

    Args:
        reason: The whole-run degradation reason under test.
        clause: Wording that reason must contribute to the sentence.
    """
    from lintro.ai.review.coverage_degradation import describe_coverage_degradations
    from lintro.ai.review.models.coverage_degradation import SYNTHESIS_CHUNK_INDEX
    from lintro.ai.review.models.review_metadata import ReviewMetadata

    metadata = ReviewMetadata(
        model="m",
        provider="p",
        context_window=1,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.FINDINGS_CAP_APPLIED,
                chunk_index=0,
                findings_cap=25,
            ),
            CoverageDegradation(
                reason=reason,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                findings_cap=0,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("1 of 1 chunk ran under a 25-finding per-call cap")
    assert_that(text).does_not_contain("of 2 chunks")
    assert_that(text).contains(clause)
    # The synthesis row's placeholder cap of 0 must never win the min(): the
    # tightest ceiling a chunk actually ran under is the cap row's 25.
    assert_that(metadata.findings_cap_applied).is_equal_to(25)
    assert_that(metadata.findings_coverage_complete).is_false()
