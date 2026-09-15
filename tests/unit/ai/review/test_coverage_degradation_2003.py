"""Output-exhaustion coverage signals (issue #2003).

A CLI review whose chunk answer exhausted the provider output ceiling was split
in two and each half reviewed on its own, so the model never saw that chunk in
one view. These tests pin that such a run is distinguishable from a clean one
in metadata, on the terminal, on both posted GitHub surfaces, and in the JSON
and MCP payloads — that a clean run still renders byte-identically to before
the signal existed — and that no per-call findings cap exists any more
(lintro-ops milestone 0, decision A): however many findings a chunk returns,
nothing is recorded against it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.chunk_split_retry import review_chunk_main_pass
from lintro.ai.review.coverage_degradation import (
    COVERAGE_LIMITED_HEADLINE,
    PARTIAL_REVIEW_LABEL,
    describe_coverage_degradations,
)
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review_async
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.response_pipeline import ChunkReviewRequest
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky import build_sticky_comment
from lintro.mcp.toolkits.review import _run_metadata

_RETRY = CoverageDegradation(
    reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
    chunk_index=0,
)
_RETRY_OTHER = CoverageDegradation(
    reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
    chunk_index=1,
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
        request=StickyRequest(
            result=result,
            transport="cli",
            auth_mode="subscription",
        ),
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


def test_clean_run_reports_complete_coverage(
    sample_review_result: ReviewResult,
) -> None:
    """A run with no recorded degradation is coverage-complete.

    Args:
        sample_review_result: Shared review result fixture.
    """
    metadata = sample_review_result.metadata

    assert_that(metadata.coverage_degradations).is_empty()
    assert_that(metadata.findings_coverage_complete).is_true()
    assert_that(metadata.output_exhaustion_retried).is_false()


def test_split_is_recorded_without_flipping_partial(
    sample_review_result: ReviewResult,
) -> None:
    """A split chunk is coverage-limited but not ``partial``.

    ``partial`` means chunks went unreviewed; a split chunk was reviewed in
    halves, so the two axes stay independent.

    Args:
        sample_review_result: Shared review result fixture.
    """
    result = _with_degradations(
        result=sample_review_result,
        degradations=(_RETRY,),
    )

    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(result.metadata.output_exhaustion_retried).is_true()
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.stopped_reason).is_equal_to("")


def test_metadata_exposes_no_findings_cap(
    sample_review_result: ReviewResult,
) -> None:
    """The per-call findings cap is gone from the metadata contract.

    Args:
        sample_review_result: Shared review result fixture.
    """
    assert_that(hasattr(CoverageDegradationReason, "FINDINGS_CAP_APPLIED")).is_false()
    assert_that(CoverageDegradation.__dataclass_fields__).does_not_contain_key(
        "findings_cap",
    )


# --- shared wording ----------------------------------------------------------


def test_description_is_empty_for_a_complete_run(
    sample_review_result: ReviewResult,
) -> None:
    """A clean run produces no coverage sentence at all.

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
        ((_RETRY,), "exhausted the provider output limit"),
        ((_RETRY, _RETRY_OTHER), "split and re-reviewed in halves"),
    ],
    ids=["case=one_split", "case=two_splits"],
)
def test_description_names_the_split(
    sample_review_result: ReviewResult,
    degradations: tuple[CoverageDegradation, ...],
    expected: str,
) -> None:
    """The shared sentence names the split and warns findings may be missing.

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


def test_terminal_banner_only_appears_for_a_split_run(
    sample_review_result: ReviewResult,
) -> None:
    """The terminal warns on a split run and is unchanged on a clean one.

    Args:
        sample_review_result: Shared review result fixture.
    """
    clean = _terminal(result=sample_review_result)
    capped = _terminal(
        result=_with_degradations(
            result=sample_review_result,
            degradations=(_RETRY, _RETRY_OTHER),
        ),
    )

    assert_that(clean).does_not_contain(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains("exhausted the provider output limit")


def test_review_body_carries_the_warning_only_when_split(
    sample_review_result: ReviewResult,
) -> None:
    """The posted review body warns on a split run, byte-identical otherwise.

    Args:
        sample_review_result: Shared review result fixture.
    """
    clean = _body(result=sample_review_result)
    capped = _body(
        result=_with_degradations(
            result=sample_review_result,
            degradations=(_RETRY,),
        ),
    )

    assert_that(clean).does_not_contain(COVERAGE_LIMITED_HEADLINE)
    assert_that(capped).contains(f"> ⚠️ **{COVERAGE_LIMITED_HEADLINE}**")
    # Production-independent copy: the detail sentence, not just the headline.
    assert_that(clean).does_not_contain("may go unreported")
    assert_that(capped).contains("exhausted the provider output limit")
    assert_that(capped).contains("may go unreported")


def test_sticky_carries_the_warning_only_when_split(
    sample_review_result: ReviewResult,
) -> None:
    """The sticky comment marks a split round, byte-identical otherwise.

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
    assert_that(capped).contains("split and re-reviewed in halves")
    assert_that(capped).contains("may go unreported")


def test_clean_run_renders_identically_on_every_surface(
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
    record = RunRecord(
        identity=RunIdentity(round=1),
        coverage=RunCoverage(coverage_limited=True),
    )

    payload = record.to_dict()

    assert_that(payload).contains_key("coverage_limited")
    assert_that(RunRecord.from_dict(payload).coverage.coverage_limited).is_true()


def test_run_record_omits_the_flag_for_a_complete_round() -> None:
    """A legacy or complete record keeps its byte-identical serialized shape."""
    payload = RunRecord(identity=RunIdentity(round=1)).to_dict()

    assert_that(payload).does_not_contain_key("coverage_limited")
    assert_that(RunRecord.from_dict(payload).coverage.coverage_limited).is_false()


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
        degradations=(_RETRY, _RETRY_OTHER),
    )

    payload = review_result_to_dict(result=result)

    assert_that(payload["findings_coverage_complete"]).is_false()
    assert_that(payload["output_exhaustion_retried"]).is_true()
    assert_that(payload["coverage_degradations"]).is_equal_to(
        [
            {"reason": "output_exhaustion_retried", "chunk_index": 0},
            {"reason": "output_exhaustion_retried", "chunk_index": 1},
        ],
    )
    # The retired cap never comes back as a payload key.
    # The reason must survive JSON encoding as a plain string, not an enum repr.
    assert_that(json.loads(json.dumps(payload))["coverage_degradations"]).is_equal_to(
        payload["coverage_degradations"],
    )


def test_json_payload_marks_a_clean_run_complete(
    sample_review_result: ReviewResult,
) -> None:
    """A clean run states completeness rather than omitting the key.

    Args:
        sample_review_result: Shared review result fixture.
    """
    payload = review_result_to_dict(result=sample_review_result)

    assert_that(payload["findings_coverage_complete"]).is_true()
    assert_that(payload["coverage_degradations"]).is_empty()
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
        degradations=(_RETRY,),
    )

    run = _run_metadata(metadata=result.metadata)

    assert_that(run["findings_coverage_complete"]).is_false()
    assert_that(run["output_exhaustion_retried"]).is_true()
    assert_that(run["coverage_degradations"]).is_length(1)
    assert_that(run).does_not_contain_key("findings_cap_applied")
    # A split chunk is not the same condition as an early stop.
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


def _ok_response(*, findings: int = 0, file: str = "src/a.py") -> AIResponse:
    """Return a minimal well-formed chunk review response.

    Args:
        findings: How many distinct findings the answer carries. No per-call
            cap exists, so any count must leave the run coverage-complete.
        file: Path the findings point at, so a split chunk's halves can be
            told apart by what they reported.

    Returns:
        A parseable provider response carrying ``findings`` findings.
    """
    payload = {
        "summary": {"headline": "Adds a constant.", "walkthrough": []},
        "checklist": [],
        "findings": [
            {
                "severity": "P3",
                "category": "style",
                "title": f"Nit {index} in {file}",
                "file": file,
                "line": index + 1,
                "description": f"Minor point {index}.",
            }
            for index in range(findings)
        ],
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


def _two_file_chunk_and_context(
    *,
    repo_root: str,
) -> tuple[ReviewChunk, ReviewContext]:
    """Build a two-file chunk whose diff carries a section per file.

    Args:
        repo_root: Absolute path used as the review's repository root.

    Returns:
        The chunk and the context that carries its diff.
    """
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n+x = 1\n"
        "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n"
        "@@ -1 +1 @@\n+y = 2\n"
    )
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py", "src/b.py"],
        diff=diff,
        relationship="directory-prefix",
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="src/a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="src/b.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=diff,
        pr_metadata=None,
        repo_root=repo_root,
    )
    return chunk, context


def _stub_provider() -> MagicMock:
    """Return a provider double the run session can close."""
    provider = MagicMock()
    # The run session closes every provider it owns (#2302), so the
    # double has to model an awaitable ``aclose``.
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False
    return provider


async def _main_pass_for(
    *,
    tmp_path: Path,
    two_files: bool,
    exhaust_calls: frozenset[int],
    prompts: list[str],
) -> ChunkReviewPartial:
    """Drive the chunk main-pass seam and return the partial it produced.

    Args:
        tmp_path: Temporary directory used as the repository root.
        two_files: Whether the chunk carries two files (splittable) or one.
        exhaust_calls: One-based provider call numbers that fail with an
            output-token exhaustion error.
        prompts: Receives the user prompt of every provider call, in order.

    Returns:
        The chunk partial, carrying whatever degradations the seam recorded.
    """
    if two_files:
        chunk, context = _two_file_chunk_and_context(repo_root=str(tmp_path))
    else:
        chunk, context = _chunk_and_context(repo_root=str(tmp_path))
    budget = MagicMock()
    budget.check = MagicMock()

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        """Fail the configured calls on output exhaustion, else answer."""
        prompt = str(kwargs.get("user_prompt", ""))
        prompts.append(prompt)
        if len(prompts) in exhaust_calls:
            raise AIProviderError(
                "Claude CLI reported error: maximum output tokens reached",
            )
        # A half's prompt carries only its own diff section; the changed-file
        # list names every file, so the diff text is what tells them apart.
        file = (
            "src/b.py" if "+y = 2" in prompt and "+x = 1" not in prompt else "src/a.py"
        )
        return _ok_response(findings=1, file=file)

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        return await review_chunk_main_pass(
            request=ChunkReviewRequest(
                chunk=chunk,
                context=context,
                provider=_stub_provider(),
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
                chunk_index=3,
            ),
        )


async def test_clean_main_pass_records_nothing(tmp_path: Path) -> None:
    """A chunk call that answers first time records no degradation.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prompts: list[str] = []
    partial = await _main_pass_for(
        tmp_path=tmp_path,
        two_files=True,
        exhaust_calls=frozenset(),
        prompts=prompts,
    )

    assert_that(prompts).is_length(1)
    assert_that(partial.coverage_degradations).is_empty()
    # No per-call ceiling is written into the prompt any more.
    assert_that(prompts[0].lower()).does_not_contain("cap `findings`")
    assert_that(prompts[0]).contains("There is no cap on findings")


async def test_exhausted_multi_file_chunk_is_split_and_merged(
    tmp_path: Path,
) -> None:
    """Output exhaustion splits a two-file chunk into two calls, one per file.

    The halves' findings merge under the original chunk, and exactly one
    ``OUTPUT_EXHAUSTION_RETRIED`` degradation is recorded for the chunk.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prompts: list[str] = []
    partial = await _main_pass_for(
        tmp_path=tmp_path,
        two_files=True,
        exhaust_calls=frozenset({1}),
        prompts=prompts,
    )

    assert_that(prompts).is_length(3)
    # The first half sees only a.py's section, the second only b.py's.
    assert_that(prompts[1]).contains("+x = 1")
    assert_that(prompts[1]).does_not_contain("+y = 2")
    assert_that(prompts[2]).contains("+y = 2")
    assert_that(prompts[2]).does_not_contain("+x = 1")
    assert_that([finding.file for finding in partial.findings]).is_equal_to(
        ["src/a.py", "src/b.py"],
    )
    reasons = [item.reason for item in partial.coverage_degradations]
    assert_that(reasons).is_equal_to(
        [CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED],
    )
    assert_that(partial.coverage_degradations[0].chunk_index).is_equal_to(3)
    # Usage is the sum of both halves.
    assert_that(partial.input_tokens).is_equal_to(20)
    assert_that(partial.output_tokens).is_equal_to(40)


async def test_exhausted_single_file_chunk_is_retried_once(
    tmp_path: Path,
) -> None:
    """A single-file chunk cannot be split, so it is retried once unchanged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prompts: list[str] = []
    partial = await _main_pass_for(
        tmp_path=tmp_path,
        two_files=False,
        exhaust_calls=frozenset({1}),
        prompts=prompts,
    )

    assert_that(prompts).is_length(2)
    # The retry is the same prompt; only the per-call boundary marker differs.
    boundary = re.compile(r"CODE_BLOCK_[0-9a-f]+")
    assert_that(boundary.sub("CODE_BLOCK", prompts[1])).is_equal_to(
        boundary.sub("CODE_BLOCK", prompts[0]),
    )
    reasons = [item.reason for item in partial.coverage_degradations]
    assert_that(reasons).is_equal_to(
        [CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED],
    )
    assert_that(partial.findings).is_length(1)


async def test_exhaustion_on_a_half_keeps_the_other_half(tmp_path: Path) -> None:
    """A half that exhausts the ceiling again is dropped, not the whole chunk.

    The surviving half's findings are kept and only its files count as
    reviewed; the failed half's files are left unreviewed for the coverage
    surfaces to report.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prompts: list[str] = []
    partial = await _main_pass_for(
        tmp_path=tmp_path,
        two_files=True,
        exhaust_calls=frozenset({1, 2}),
        prompts=prompts,
    )

    assert_that(prompts).is_length(3)
    assert_that(partial.files).is_equal_to(("src/b.py",))
    assert_that([finding.file for finding in partial.findings]).is_equal_to(
        ["src/b.py"],
    )
    reasons = [item.reason for item in partial.coverage_degradations]
    assert_that(reasons).is_equal_to(
        [
            CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
            CoverageDegradationReason.SPLIT_HALF_FAILED,
        ],
    )


async def test_exhaustion_on_both_halves_propagates(tmp_path: Path) -> None:
    """With neither half answering, the provider error is raised.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prompts: list[str] = []
    with pytest.raises(AIProviderError, match="maximum output tokens"):
        await _main_pass_for(
            tmp_path=tmp_path,
            two_files=True,
            exhaust_calls=frozenset({1, 2, 3}),
            prompts=prompts,
        )

    assert_that(prompts).is_length(3)


#: Findings count large enough that the old default cap (12) would have bitten.
_MANY_FINDINGS = 25


async def _cli_run(
    *,
    tmp_path: Path,
    findings: int,
    exhaust_first_call: bool = False,
) -> ReviewResult:
    """Run a CLI review whose replayed chunk answers carry ``findings`` each.

    Args:
        tmp_path: Temporary directory used as the repository root.
        findings: How many findings each replayed chunk answer returns.
        exhaust_first_call: When True, the review context carries two files
            and the first provider call fails with output exhaustion, so the
            chunk is split and each half answered.

    Returns:
        The completed review result.
    """
    if exhaust_first_call:
        _chunk, context = _two_file_chunk_and_context(repo_root=str(tmp_path))
    else:
        _chunk, context = _chunk_and_context(repo_root=str(tmp_path))
    calls: list[int] = []

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        calls.append(1)
        if exhaust_first_call and len(calls) == 1:
            raise AIProviderError(
                "Claude CLI reported error: maximum output tokens reached",
            )
        return _ok_response(findings=findings)

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        return await run_review_async(
            context=context,
            options=ReviewSessionOptions(
                provider=_stub_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    transport=AITransport.CLI,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
            ),
        )


async def test_cli_run_records_no_cap_however_many_findings(
    tmp_path: Path,
) -> None:
    """A chunk answering with many findings records no degradation at all.

    Before lintro-ops milestone 0 a chunk answering with exactly the configured
    ceiling recorded a cap hit and reddened the check. There is no ceiling:
    a chunk reports every finding it has and the run stays complete.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _cli_run(tmp_path=tmp_path, findings=_MANY_FINDINGS)

    assert_that(result.findings).is_length(_MANY_FINDINGS)
    assert_that(result.metadata.coverage_degradations).is_empty()
    assert_that(result.metadata.findings_coverage_complete).is_true()
    assert_that(result.metadata.partial).is_false()


async def test_cli_run_that_exhausts_output_records_the_split_end_to_end(
    tmp_path: Path,
) -> None:
    """A split chunk reaches ``ReviewMetadata`` as one degradation.

    Locks the orchestrator wiring: if the per-chunk degradation stops being
    aggregated onto ``ReviewMetadata``, a re-reviewed chunk would present as
    an untouched one again.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _cli_run(tmp_path=tmp_path, findings=1, exhaust_first_call=True)

    assert_that(result.metadata.coverage_degradations).is_length(1)
    recorded = result.metadata.coverage_degradations[0]
    assert_that(recorded.reason).is_equal_to(
        CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
    )
    assert_that(recorded.chunk_index).is_equal_to(0)
    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(result.metadata.output_exhaustion_retried).is_true()
    assert_that(result.metadata.partial).is_false()
    assert_that(
        describe_coverage_degradations(metadata=result.metadata),
    ).contains("1 of 1 chunk exhausted the provider output limit")


#: The CI classifier that turns a review envelope into a check outcome. It is
#: a script, not an importable package module, so it is loaded by path.
_CLASSIFIER_SCRIPT = (
    Path(__file__).resolve().parents[4]
    / "scripts"
    / "ci"
    / "classify_review_outcome.py"
)


def _classify_run(*, result: ReviewResult) -> Any:
    """Classify a real run's JSON envelope the way the CI check does.

    Args:
        result: The review result whose ``--output json`` envelope is classified.

    Returns:
        The classifier's ``OutcomeReport``. Typed loosely because the
        classifier is a CI script loaded by path, not an importable module.

    Raises:
        RuntimeError: When the classifier script cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(
        "classify_review_outcome_2283",
        _CLASSIFIER_SCRIPT,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_CLASSIFIER_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the script's dataclasses resolve their
    # string annotations through ``sys.modules`` at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.classify(
        status=0,
        output=json.dumps(review_result_to_dict(result=result)),
        transport="cli",
    )


async def test_many_findings_cli_run_classifies_as_reviewed(tmp_path: Path) -> None:
    """The CI check is green for a CLI round however many findings it found.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _cli_run(tmp_path=tmp_path, findings=_MANY_FINDINGS)

    report = _classify_run(result=result)

    assert_that(report.outcome.value).is_equal_to("reviewed")
    assert_that(report.exit_code).is_equal_to(0)


async def test_cli_run_that_split_a_chunk_classifies_as_degraded(
    tmp_path: Path,
) -> None:
    """A split chunk still reddens the CI check.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _cli_run(tmp_path=tmp_path, findings=1, exhaust_first_call=True)

    report = _classify_run(result=result)

    assert_that(report.outcome.value).is_equal_to("degraded")
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.detail).contains("output_exhaustion_retried")


async def test_many_findings_cli_run_renders_like_a_clean_run(
    tmp_path: Path,
) -> None:
    """Every surface of a many-findings CLI run matches a clean counterpart.

    Terminal, JSON, review body and sticky are compared byte for byte against
    the same result with an explicitly empty degradation tuple — there is no
    cap-shaped difference between transports any more.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    result = await _cli_run(tmp_path=tmp_path, findings=_MANY_FINDINGS)
    uncapped = _with_degradations(result=result, degradations=())

    assert_that(_terminal(result=result)).is_equal_to(_terminal(result=uncapped))
    assert_that(_body(result=result)).is_equal_to(_body(result=uncapped))
    assert_that(_sticky(result=result)).is_equal_to(_sticky(result=uncapped))
    assert_that(review_result_to_dict(result=result)).is_equal_to(
        review_result_to_dict(result=uncapped),
    )
    for surface in (
        _terminal(result=result),
        _body(result=result),
        _sticky(result=result),
    ):
        assert_that(surface).does_not_contain(COVERAGE_LIMITED_HEADLINE)
        assert_that(surface).does_not_contain(PARTIAL_REVIEW_LABEL)


def test_partial_and_split_run_does_not_claim_every_chunk_reviewed() -> None:
    """A run that is both split and stopped early never over-claims coverage."""
    from lintro.ai.review.coverage_degradation import describe_coverage_degradations
    from lintro.ai.review.enums.coverage_degradation_reason import (
        CoverageDegradationReason,
    )
    from lintro.ai.review.models.coverage_degradation import CoverageDegradation
    from lintro.ai.review.models.review_metadata import ReviewMetadata

    capped = CoverageDegradation(
        reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
        chunk_index=0,
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
    assert_that(text).contains("Findings that need the whole chunk in view")


def test_run_record_coverage_limited_uses_strict_bool_parsing() -> None:
    """A string ``"false"`` in a legacy blob must not read as limited."""
    from lintro.ai.review.models.run_record import RunRecord

    base = RunRecord().to_dict()

    assert_that(
        RunRecord.from_dict(
            {**base, "coverage_limited": "false"},
        ).coverage.coverage_limited,
    ).is_false()
    assert_that(
        RunRecord.from_dict(
            {**base, "coverage_limited": True},
        ).coverage.coverage_limited,
    ).is_true()
    assert_that(RunRecord.from_dict(base).coverage.coverage_limited).is_false()


def test_split_and_failed_pass_on_one_chunk_count_once_in_the_description() -> None:
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
                reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
                chunk_index=0,
            ),
            CoverageDegradation(
                reason=CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED,
                chunk_index=0,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("1 of 1 chunk exhausted the provider output limit")
    assert_that(text).contains("1 chunk kept only the main pass")
    assert_that(text).does_not_contain("of 2 chunks")


def test_run_record_partial_uses_strict_bool_parsing() -> None:
    """The legacy ``partial`` flag gets the same string-safe parsing."""
    from lintro.ai.review.models.run_record import RunRecord

    base = RunRecord().to_dict()

    assert_that(
        RunRecord.from_dict({**base, "partial": "false"}).coverage.partial,
    ).is_false()
    assert_that(
        RunRecord.from_dict({**base, "partial": True}).coverage.partial,
    ).is_true()


def test_sticky_history_marks_a_prior_limited_round(
    sample_review_result: ReviewResult,
) -> None:
    """The run-history recap keeps a limited round visible in later rounds.

    Args:
        sample_review_result: Shared review result fixture.
    """
    from lintro.ai.review.models.run_record import RunRecord
    from lintro.ai.review.sticky import build_sticky_bodies

    limited = RunRecord(
        identity=RunIdentity(round=1, sha="abc1234"),
        coverage=RunCoverage(coverage_limited=True),
    ).to_dict()
    unlimited = RunRecord(identity=RunIdentity(round=1, sha="abc1234")).to_dict()

    # The primary sticky archives run history into its companion body, so the
    # marker is asserted across both bodies the public builder returns.
    with_marker = "\n".join(
        body or ""
        for body in build_sticky_bodies(
            request=StickyRequest(
                result=sample_review_result,
                prior_state=ReviewState(runs=(RunRecord.from_dict(limited),)),
                transport="cli",
            ),
        )
    )
    without_marker = "\n".join(
        body or ""
        for body in build_sticky_bodies(
            request=StickyRequest(
                result=sample_review_result,
                prior_state=ReviewState(runs=(RunRecord.from_dict(unlimited),)),
                transport="cli",
            ),
        )
    )

    assert_that(with_marker).contains("Run-by-run history")
    assert_that(with_marker).contains("⚠️ coverage limited")
    assert_that(without_marker).does_not_contain("⚠️ coverage limited")


def test_advanced_state_persists_coverage_limited_from_a_split_result(
    sample_review_result: ReviewResult,
) -> None:
    """A split result stamps coverage_limited on the persisted run record.

    Args:
        sample_review_result: Shared review result fixture.
    """
    from lintro.ai.review.models.run_record import RunRecord
    from lintro.ai.review.sticky import advance_review_state

    capped_state = advance_review_state(
        request=StickyRequest(
            result=_with_degradations(
                result=sample_review_result,
                degradations=(_RETRY,),
            ),
            head_sha="abc1234",
            transport="cli",
        ),
    )
    clean_state = advance_review_state(
        request=StickyRequest(
            result=sample_review_result,
            head_sha="abc1234",
            transport="cli",
        ),
    )

    capped_run = capped_state.runs[-1]
    assert_that(capped_run.coverage.coverage_limited).is_true()
    assert_that(clean_state.runs[-1].coverage.coverage_limited).is_false()
    # The flag survives the flat persisted shape.
    assert_that(
        RunRecord.from_dict(capped_run.to_dict()).coverage.coverage_limited,
    ).is_true()


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

    novel = replace(_RETRY, reason=_Novel())  # type: ignore[arg-type]
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
    the denominator.

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
                reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
                chunk_index=0,
            ),
            CoverageDegradation(
                reason=reason,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("1 of 1 chunk exhausted the provider output limit")
    assert_that(text).does_not_contain("of 2 chunks")
    assert_that(text).contains(clause)
    assert_that(metadata.findings_coverage_complete).is_false()
