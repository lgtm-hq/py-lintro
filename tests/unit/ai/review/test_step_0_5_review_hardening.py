"""Hardening of the step 0.5 review shape (lintro-ops #37, Codex review round).

Pins seven behaviours the first Codex pass over the findings-only chunk shape
found missing: an output-exhaustion error is never retried generically, a
failed half of a split chunk does not discard the other half, env and CLI
overlays keep the user's explicit-field set (so the CLI parallelism clamp
still applies under ``LINTRO_AI_TRANSPORT=cli``), overlapping duplicate groups
resolve against live survivors, a synthesis answer without its narrative is
flagged, a single over-target file is reviewed whole up to the context window
and recorded when cut, and a sticky nit row carries enough to act on.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.cli_schemas import SYNTHESIS_CLI_SCHEMA
from lintro.ai.config import AIConfig
from lintro.ai.config_overrides import apply_env_overrides
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError, AIProviderError
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.retry import with_retry
from lintro.ai.review.chunk_split_retry import review_chunk_main_pass
from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage_degradation import describe_coverage_degradations
from lintro.ai.review.coverage_rounds import (
    hashes_for_diffs,
    latest_coverage_by_path,
    truncated_patch_hashes,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.posting_policy import PostingPolicy, apply_posting_policy
from lintro.ai.review.response_pipeline import ChunkReviewRequest
from lintro.ai.review.resume import (
    carried_truncated_paths,
    plan_resume,
    records_for_reviewed,
)
from lintro.ai.review.run_planning import (
    resolve_max_parallel_calls,
    resolve_review_chunks,
)
from lintro.ai.review.sticky import build_sticky_comment
from lintro.ai.review.synthesis_narrative import (
    DuplicateGroup,
    apply_duplicate_groups,
)
from lintro.config.review_config import ReviewSynthesisConfig
from tests.unit.ai.review.review_fixtures import make_review_context
from tests.unit.ai.review.test_cross_chunk_synthesis_2269 import (
    _outcome,
    _pr_context,
    _run,
    _synthesis_payload,
    _two_chunks,
)

_EXHAUSTED = "Claude CLI reported error: maximum output tokens reached"


# --- 1. output exhaustion is not a transient failure ---------------------------


@patch("lintro.ai.retry.asyncio.sleep")
async def test_output_exhaustion_is_raised_on_the_first_attempt(
    mock_sleep: MagicMock,
) -> None:
    """The generic retry loop does not repeat an oversized request.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    calls = 0

    @with_retry(max_retries=3, base_delay=0.0)
    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise AIProviderError(_EXHAUSTED)

    with pytest.raises(AIProviderError):
        await fn()

    assert_that(calls).is_equal_to(1)
    assert_that(mock_sleep.call_count).is_equal_to(0)


@patch("lintro.ai.retry.asyncio.sleep")
async def test_other_provider_errors_still_retry(mock_sleep: MagicMock) -> None:
    """A transient provider error keeps its retry budget.

    Args:
        mock_sleep: Patched ``asyncio.sleep``.
    """
    calls = 0

    @with_retry(max_retries=2, base_delay=0.0)
    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise AIProviderError("server error")
        return "ok"

    assert_that(await fn()).is_equal_to("ok")
    assert_that(calls).is_equal_to(3)


# --- 2. a failed half keeps the other half -------------------------------------


def _two_file_chunk(*, repo_root: str) -> tuple[ReviewChunk, ReviewContext]:
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


def _ok_response(*, file: str) -> AIResponse:
    return AIResponse(
        content=(
            '{"findings": [{"severity": "P2", "category": "logic-bug", '
            f'"file": "{file}", "line": 1, "title": "t", "description": "d", '
            '"cause": "c", "fix": "f", "confidence": "high", '
            '"checklist_ids": []}], "flagged_files": []}'
        ),
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.0,
    )


async def _split_with(
    *,
    tmp_path: Path,
    failures: dict[int, Exception],
) -> Any:
    """Drive the main pass with the given per-call failures (1-based)."""
    chunk, context = _two_file_chunk(repo_root=str(tmp_path))
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False
    budget = MagicMock()
    budget.check = MagicMock()
    prompts: list[str] = []

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        prompt = str(kwargs.get("user_prompt", ""))
        prompts.append(prompt)
        error = failures.get(len(prompts))
        if error is not None:
            raise error
        file = (
            "src/b.py" if "+y = 2" in prompt and "+x = 1" not in prompt else "src/a.py"
        )
        return _ok_response(file=file)

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        return await review_chunk_main_pass(
            request=ChunkReviewRequest(
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
                chunk_index=3,
            ),
        )


async def test_a_failed_second_half_keeps_the_first_half(tmp_path: Path) -> None:
    """The first half's findings survive; the failed half's files are unreviewed.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    partial = await _split_with(
        tmp_path=tmp_path,
        failures={1: AIProviderError(_EXHAUSTED), 3: AIProviderError("timeout")},
    )

    assert_that([finding.file for finding in partial.findings]).is_equal_to(
        ["src/a.py"],
    )
    assert_that(partial.files).is_equal_to(("src/a.py",))
    assert_that([item.reason for item in partial.coverage_degradations]).is_equal_to(
        [
            CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
            CoverageDegradationReason.SPLIT_HALF_FAILED,
        ],
    )
    # The loss names only the failed half's files, so a rerun redoes those
    # and not the half that was reviewed (#2803).
    assert_that(partial.coverage_degradations[-1].paths).is_equal_to(("src/b.py",))
    assert_that(partial.input_tokens).is_equal_to(10)


def test_a_lost_half_is_described_as_unreviewed_files(
    sample_review_result: ReviewResult,
) -> None:
    """The describer names the lost half and never claims every chunk.

    Args:
        sample_review_result: Shared review result fixture.
    """
    metadata = replace(
        sample_review_result.metadata,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED,
                chunk_index=0,
            ),
            CoverageDegradation(
                reason=CoverageDegradationReason.SPLIT_HALF_FAILED,
                chunk_index=0,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("lost one half to a failed call")
    assert_that(text).contains("the files in that half were not reviewed")
    assert_that(text).does_not_contain("Every chunk was reviewed")
    assert_that(text).does_not_contain("other limit")
    assert_that(metadata.findings_coverage_complete).is_false()


async def test_both_halves_failing_raises(tmp_path: Path) -> None:
    """With nothing to keep, the provider error propagates.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    with pytest.raises(AIProviderError, match="timeout"):
        await _split_with(
            tmp_path=tmp_path,
            failures={
                1: AIProviderError(_EXHAUSTED),
                2: AIProviderError("timeout"),
                3: AIProviderError("timeout"),
            },
        )


async def test_a_cost_cap_stop_on_a_half_propagates(tmp_path: Path) -> None:
    """A budget stop is never swallowed into a degraded half.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    with pytest.raises(AICostBudgetExceededError):
        await _split_with(
            tmp_path=tmp_path,
            failures={
                1: AIProviderError(_EXHAUSTED),
                3: AICostBudgetExceededError("cap"),
            },
        )


# --- 3. overlays keep the explicit-field set -----------------------------------


def test_env_overlay_keeps_the_cli_parallelism_clamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_AI_TRANSPORT=cli`` alone must not mark every field as set.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for name in (
        "LINTRO_AI_ENABLED",
        "LINTRO_AI_REVIEW",
        "LINTRO_AI_PROVIDER",
        "LINTRO_AI_MODEL",
        "LINTRO_AI_MAX_COST_USD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "cli")

    overlaid, _sources = apply_env_overrides(AIConfig(enabled=True, review=True), {})

    assert_that(overlaid.transport).is_equal_to(AITransport.CLI)
    assert_that(overlaid.model_fields_set).contains("transport", "enabled", "review")
    assert_that(overlaid.model_fields_set).does_not_contain("max_parallel_calls")
    assert_that(
        resolve_max_parallel_calls(ai_config=overlaid, enforce_cost_cap=False),
    ).is_equal_to(3)


def test_env_overlay_keeps_an_explicit_parallelism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-set ``max_parallel_calls`` survives the overlay as explicit.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_AI_TRANSPORT", "cli")

    overlaid, _sources = apply_env_overrides(
        AIConfig(enabled=True, review=True, max_parallel_calls=5),
        {},
    )

    assert_that(overlaid.model_fields_set).contains("max_parallel_calls")
    assert_that(
        resolve_max_parallel_calls(ai_config=overlaid, enforce_cost_cap=False),
    ).is_equal_to(5)


# --- 4. overlapping duplicate groups -------------------------------------------


def _finding(*, file: str, line: int, severity: Severity, title: str) -> ReviewFinding:
    return ReviewFinding(
        severity=severity,
        category="logic-bug",
        file=file,
        line=line,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def test_overlapping_groups_redirect_to_the_live_survivor() -> None:
    """A group naming an already-dropped finding folds into its survivor."""
    findings = (
        _finding(file="a.py", line=1, severity=Severity.P2, title="A"),
        _finding(file="b.py", line=2, severity=Severity.P1, title="B"),
        _finding(file="c.py", line=3, severity=Severity.P3, title="C"),
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(
            DuplicateGroup(keep="a.py:1", drop=("b.py:2",)),  # B (P1) survives
            DuplicateGroup(keep="a.py:1", drop=("c.py:3",)),  # A is gone; C → B
        ),
    )

    assert_that(merged).is_equal_to(2)
    assert_that([finding.title for finding in kept]).is_equal_to(["B"])
    assert_that([o.label for o in kept[0].all_occurrences]).is_equal_to(
        ["b.py:2", "a.py:1", "c.py:3"],
    )


def test_chained_groups_carry_absorbed_sites_forward() -> None:
    """Dropping a survivor into a later group moves what it absorbed too."""
    findings = (
        _finding(file="a.py", line=1, severity=Severity.P3, title="A"),
        _finding(file="b.py", line=2, severity=Severity.P3, title="B"),
        _finding(file="c.py", line=3, severity=Severity.P1, title="C"),
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(
            DuplicateGroup(keep="a.py:1", drop=("b.py:2",)),  # B → A
            DuplicateGroup(keep="c.py:3", drop=("a.py:1",)),  # A → C, with B
        ),
    )

    assert_that(merged).is_equal_to(2)
    assert_that([finding.title for finding in kept]).is_equal_to(["C"])
    assert_that([o.label for o in kept[0].all_occurrences]).is_equal_to(
        ["c.py:3", "a.py:1", "b.py:2"],
    )


# --- 5. the synthesis narrative is required ------------------------------------


def test_synthesis_cli_schema_requires_the_narrative() -> None:
    """A structured CLI reply cannot omit the summary or the reasoning."""
    assert_that(SYNTHESIS_CLI_SCHEMA["required"]).contains(
        "summary",
        "verdict_reasoning",
        "findings",
    )
    assert_that(SYNTHESIS_CLI_SCHEMA["required"]).does_not_contain("duplicates")


def test_synthesis_outcome_serializes_narrative_missing() -> None:
    """The JSON block says when the pass wrote no summary."""
    assert_that(SynthesisOutcome().to_dict()["narrative_missing"]).is_false()
    assert_that(
        SynthesisOutcome(narrative_missing=True).to_dict()["narrative_missing"],
    ).is_true()


def test_a_summary_less_synthesis_answer_is_flagged_not_silent() -> None:
    """A findings-only synthesis reply completes but is marked narrative-missing."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=_synthesis_payload(),
    )

    outcome = _outcome(result=result)
    assert_that(outcome.failed).is_false()
    assert_that(outcome.narrative_missing).is_true()
    assert_that(result.pr_summary).is_none()


def test_a_verdict_less_synthesis_answer_is_flagged_not_silent() -> None:
    """A summary without verdict reasoning is still a missing narrative."""
    payload = json.loads(_synthesis_payload())
    payload["summary"] = {"headline": "Adds a thing.", "walkthrough": []}
    payload.pop("verdict_reasoning", None)
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=json.dumps(payload),
    )

    outcome = _outcome(result=result)
    assert_that(outcome.failed).is_false()
    assert_that(outcome.narrative_missing).is_true()


def test_a_truncated_chunk_is_credited_with_a_truncated_record() -> None:
    """Round 1: the cut file counts as reviewed, but its record says cut.

    Crediting the file is what lets the round converge; the record's marker
    is what keeps the gap honest on every later round.
    """
    chunks = _two_chunks()
    chunks[0] = replace(chunks[0], truncated=True)
    result = _run(synthesis=ReviewSynthesisConfig(enabled=False), chunks=chunks)

    cut_file = chunks[0].files[0]
    assert_that(result.metadata.reviewed_paths).contains(cut_file, chunks[1].files[0])
    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(
        [item.reason for item in result.metadata.coverage_degradations],
    ).contains(CoverageDegradationReason.DIFF_TRUNCATED)
    by_path = {record.path: record for record in result.coverage_records}
    assert_that(by_path[cut_file].truncated).is_true()
    assert_that(by_path[chunks[1].files[0]].truncated).is_false()


def _prior_state_with_truncated_record(*, path: str) -> ReviewState:
    """Build a prior state whose record for ``path`` at HEAD is truncated.

    Args:
        path: The changed file the earlier round reviewed only in part.

    Returns:
        A state carrying one truncated coverage record at the current hash.
    """
    context = _pr_context()
    hashes = hashes_for_diffs(
        diffs=split_unified_diff_by_file(unified_diff=context.unified_diff),
    )
    return ReviewState(
        coverage=(
            CoverageRecord(
                path=path,
                patch_hash=hashes[path],
                reviewed_sha="head",
                round=1,
                truncated=True,
            ),
        ),
    )


def test_a_carried_truncated_file_re_reports_the_gap() -> None:
    """Round 2, same head: the cut file is skipped as covered, gap re-recorded."""
    chunks = _two_chunks()
    cut_file = chunks[0].files[0]
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=False),
        chunks=[chunks[1]],
        prior_state=_prior_state_with_truncated_record(path=cut_file),
    )

    assert_that(result.metadata.reviewed_paths).does_not_contain(cut_file)
    assert_that(result.metadata.findings_coverage_complete).is_false()
    carried = [
        item
        for item in result.metadata.coverage_degradations
        if item.reason is CoverageDegradationReason.DIFF_TRUNCATED
    ]
    assert_that(carried).is_length(1)
    assert_that(carried[0].chunk_index).is_equal_to(CARRIED_CHUNK_INDEX)
    by_path = {record.path: record for record in result.coverage_records}
    assert_that(by_path[cut_file].truncated).is_true()
    text = describe_coverage_degradations(metadata=result.metadata)
    assert_that(text).contains("carried from an earlier round")
    assert_that(text).contains("a change to the file re-reviews it")


def test_a_changed_truncated_file_is_re_reviewed_and_cleared() -> None:
    """Round 3, new diff: the file is queued again and its marker clears."""
    context = _pr_context()
    cut_file = "pkg/api.py"
    prior = _prior_state_with_truncated_record(path=cut_file)
    # The same file with a different (smaller) change: a new hash.
    changed = replace(
        context,
        unified_diff=context.unified_diff.replace(
            "+def send(payload, *, retries):",
            "+def send(payload, retries=3):",
        ),
    )

    plan = plan_resume(context=changed, prior=prior)

    assert_that(plan.queue).contains(cut_file)
    assert_that(carried_truncated_paths(plan=plan, prior=prior)).is_empty()
    records = records_for_reviewed(
        plan=plan,
        reviewed_paths=plan.queue,
        head_sha="head2",
        round_number=2,
        prior=prior,
    )
    latest = latest_coverage_by_path(records)
    assert_that(latest[cut_file].truncated).is_false()
    assert_that(latest[cut_file].round).is_equal_to(2)
    # And once that record is carried, nothing is re-reported.
    later = plan_resume(context=changed, prior=replace(prior, coverage=records))
    assert_that(later.queue).does_not_contain(cut_file)
    assert_that(
        carried_truncated_paths(plan=later, prior=replace(prior, coverage=records)),
    ).is_empty()


def test_same_hash_siblings_inherit_the_truncation_marker() -> None:
    """A sibling credited through a truncated representative is cut too."""
    context = _pr_context()
    diff = context.unified_diff.replace("pkg/api.py", "pkg/api_copy.py")
    twin = make_review_context(
        unified_diff=context.unified_diff + diff,
        changed_files=[
            *context.changed_files,
            ChangedFile(
                path="pkg/api_copy.py",
                status="modified",
                additions=1,
                deletions=1,
            ),
        ],
    )
    plan = plan_resume(context=twin, prior=None)
    assert_that(plan.hashes["pkg/api.py"]).is_equal_to(plan.hashes["pkg/api_copy.py"])

    records = records_for_reviewed(
        plan=plan,
        reviewed_paths=("pkg/api.py", "pkg/api_copy.py", "pkg/caller.py"),
        head_sha="head",
        round_number=1,
        prior=None,
        truncated_paths={"pkg/api.py"},
    )
    by_path = {record.path: record for record in records}
    assert_that(by_path["pkg/api.py"].truncated).is_true()
    assert_that(by_path["pkg/api_copy.py"].truncated).is_true()
    assert_that(by_path["pkg/caller.py"].truncated).is_false()

    # A later round that carries only the representative's record still
    # reports the sibling, which inherited coverage by hash.
    prior = ReviewState(coverage=(by_path["pkg/api.py"],))
    later = plan_resume(context=twin, prior=prior)
    assert_that(carried_truncated_paths(plan=later, prior=prior)).contains(
        "pkg/api.py",
        "pkg/api_copy.py",
    )


def _twin_context() -> ReviewContext:
    """Return the PR context with an identical-diff sibling of ``pkg/api.py``."""
    context = _pr_context()
    diff = context.unified_diff.replace("pkg/api.py", "pkg/api_copy.py")
    return make_review_context(
        unified_diff=context.unified_diff + diff,
        changed_files=[
            *context.changed_files,
            ChangedFile(
                path="pkg/api_copy.py",
                status="modified",
                additions=1,
                deletions=1,
            ),
        ],
    )


def test_an_old_truncated_hash_still_marks_a_later_sibling() -> None:
    """A path moving on to a new hash does not forget its old truncated one."""
    twin = _twin_context()
    plan = plan_resume(context=twin, prior=None)
    shared_hash = plan.hashes["pkg/api.py"]
    prior = ReviewState(
        coverage=(
            # Round 1: pkg/api.py reviewed only in part at the shared hash.
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r1",
                round=1,
                truncated=True,
            ),
            # Round 2: the same path re-reviewed whole at a different hash, so
            # its latest record no longer carries the shared hash.
            CoverageRecord(
                path="pkg/api.py",
                patch_hash="other-hash",
                reviewed_sha="r2",
                round=2,
            ),
        ),
    )
    # Round 3: the sibling appears at the shared hash and inherits coverage
    # from the round-1 prefix review, which must still count as truncated.
    later = plan_resume(context=twin, prior=prior)
    assert_that(carried_truncated_paths(plan=later, prior=prior)).contains(
        "pkg/api_copy.py",
    )


def test_a_complete_review_at_the_hash_clears_every_sibling() -> None:
    """A later complete review at a truncated hash clears the marker for all."""
    twin = _twin_context()
    plan = plan_resume(context=twin, prior=None)
    shared_hash = plan.hashes["pkg/api.py"]
    prior = ReviewState(
        coverage=(
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r1",
                round=1,
                truncated=True,
            ),
            CoverageRecord(
                path="pkg/api_copy.py",
                patch_hash=shared_hash,
                reviewed_sha="r3",
                round=3,
            ),
        ),
    )
    later = plan_resume(context=twin, prior=prior)
    assert_that(carried_truncated_paths(plan=later, prior=prior)).is_empty()


def test_a_files_own_complete_record_beats_a_stale_sibling_marker() -> None:
    """A file's own newer complete record beats an older sibling marker."""
    twin = _twin_context()
    plan = plan_resume(context=twin, prior=None)
    shared_hash = plan.hashes["pkg/api.py"]
    prior = ReviewState(
        coverage=(
            # Round 1: pkg/api.py reviewed only in part at the shared hash.
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r1",
                round=1,
                truncated=True,
            ),
            # Round 2: a sibling at the same hash, also cut, and never redone.
            CoverageRecord(
                path="pkg/api_copy.py",
                patch_hash=shared_hash,
                reviewed_sha="r2",
                round=2,
                truncated=True,
            ),
            # Round 3: pkg/api.py re-reviewed whole at the same hash.
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r3",
                round=3,
            ),
        ),
    )
    later = plan_resume(context=twin, prior=prior)
    carried = carried_truncated_paths(plan=later, prior=prior)
    # The file's own latest record is authoritative for the file itself ...
    assert_that(carried).does_not_contain("pkg/api.py")
    # ... and the sibling's identical content was completely reviewed in a
    # later round than its own cut record, so it is clear as well.
    assert_that(carried).is_empty()

    # A cut record newer than the file's own complete one wins again: the
    # ceiling may have shrunk, so the gap is reported rather than hidden.
    stale_after = ReviewState(
        coverage=(
            *prior.coverage,
            CoverageRecord(
                path="pkg/api_copy.py",
                patch_hash=shared_hash,
                reviewed_sha="r4",
                round=4,
                truncated=True,
            ),
        ),
    )
    later = plan_resume(context=twin, prior=stale_after)
    assert_that(carried_truncated_paths(plan=later, prior=stale_after)).is_equal_to(
        ("pkg/api.py", "pkg/api_copy.py"),
    )


def test_a_sibling_without_its_own_record_falls_back_to_the_hash() -> None:
    """A path with no record at its hash inherits the newest sibling verdict."""
    twin = _twin_context()
    plan = plan_resume(context=twin, prior=None)
    shared_hash = plan.hashes["pkg/api.py"]
    marked = ReviewState(
        coverage=(
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r1",
                round=1,
                truncated=True,
            ),
        ),
    )
    later = plan_resume(context=twin, prior=marked)
    assert_that(carried_truncated_paths(plan=later, prior=marked)).contains(
        "pkg/api_copy.py",
    )
    cleared = ReviewState(
        coverage=(
            *marked.coverage,
            CoverageRecord(
                path="pkg/api.py",
                patch_hash=shared_hash,
                reviewed_sha="r2",
                round=2,
            ),
        ),
    )
    later = plan_resume(context=twin, prior=cleared)
    assert_that(carried_truncated_paths(plan=later, prior=cleared)).is_empty()


def test_truncated_patch_hashes_prefers_the_marked_record_on_a_round_tie() -> None:
    """Same-round records at one hash never hide a gap behind an unmarked twin."""
    records = (
        CoverageRecord(path="a.py", patch_hash="h", round=2),
        CoverageRecord(path="b.py", patch_hash="h", round=2, truncated=True),
        CoverageRecord(path="c.py", patch_hash="k", round=1, truncated=True),
        CoverageRecord(path="d.py", patch_hash="k", round=2),
    )
    assert_that(truncated_patch_hashes(records)).is_equal_to(frozenset({"h"}))


def test_coverage_record_truncation_round_trips_and_defaults_off() -> None:
    """The marker is written only when set and an old record loads as unset."""
    record = CoverageRecord(path="a.py", patch_hash="h", truncated=True)
    assert_that(record.to_dict()["truncated"]).is_true()
    loaded = CoverageRecord.from_dict(record.to_dict())
    assert loaded is not None
    assert_that(loaded.truncated).is_true()

    plain = CoverageRecord(path="a.py", patch_hash="h")
    assert_that(plain.to_dict()).does_not_contain_key("truncated")
    old_format = CoverageRecord.from_dict({"path": "a.py", "hash": "h", "round": 1})
    assert old_format is not None
    assert_that(old_format.truncated).is_false()


# --- 6. a single over-target file --------------------------------------------


def _big_single_file_context(*, lines: int) -> ReviewContext:
    body = "".join(
        f"+line {index:05d} of a very long change here\n" for index in range(lines)
    )
    diff = (
        "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n"
        f"@@ -1 +1,{lines} @@\n{body}"
    )
    return make_review_context(
        unified_diff=diff,
        changed_files=[
            ChangedFile(path="big.py", status="modified", additions=lines, deletions=0),
        ],
    )


def test_a_single_file_over_the_target_is_reviewed_whole_under_the_ceiling() -> None:
    """A 9k-token file is one whole chunk when the context window allows it."""
    context = _big_single_file_context(lines=900)  # ~9k tokens
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=7_000,
        classifications=classifications,
        hard_max_tokens=100_000,
    )

    assert_that(result.chunks).is_length(1)
    assert_that(result.truncated).is_false()
    assert_that(result.chunks[0].truncated).is_false()
    assert_that(result.chunks[0].diff).contains("line 00899")


def test_a_single_file_over_the_ceiling_is_cut_and_marked() -> None:
    """Above the hard ceiling the file is truncated and the chunk says so."""
    context = _big_single_file_context(lines=900)
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=7_000,
        classifications=classifications,
        hard_max_tokens=8_000,
    )

    assert_that(result.chunks).is_length(1)
    assert_that(result.truncated).is_true()
    assert_that(result.chunks[0].truncated).is_true()
    assert_that(result.chunks[0].diff).does_not_contain("line 00899")


def test_resolve_review_chunks_threads_the_hard_ceiling() -> None:
    """The planner passes the context-window remainder to the chunker."""
    context = _big_single_file_context(lines=900)
    classifications = classify_changed_files(files=context.changed_files)

    chunks = resolve_review_chunks(
        context=context,
        diff_budget=7_000,
        classifications=classifications,
        hard_diff_ceiling=100_000,
    )

    assert_that(chunks).is_length(1)
    assert_that(chunks[0].truncated).is_false()
    assert_that(chunks[0].diff).contains("line 00899")


def test_a_cut_diff_is_described_as_a_coverage_limit(
    sample_review_result: ReviewResult,
) -> None:
    """``diff_truncated`` reads as a real coverage degradation.

    Args:
        sample_review_result: Shared review result fixture.
    """
    metadata = replace(
        sample_review_result.metadata,
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.DIFF_TRUNCATED,
                chunk_index=0,
            ),
        ),
    )

    text = describe_coverage_degradations(metadata=metadata)

    assert_that(text).contains("diff cut to the context window")
    assert_that(text).does_not_contain("other limit")


# --- 7. sticky nit rows carry description and fix ------------------------------


def test_sticky_nit_row_carries_description_and_fix(
    sample_review_result: ReviewResult,
) -> None:
    """A P3 row is actionable without an inline thread.

    Args:
        sample_review_result: Shared review result fixture.
    """
    nit = apply_posting_policy(
        findings=(
            ReviewFinding(
                severity=Severity.P3,
                category="code-smell",
                file="src/app.py",
                line=9,
                title="Nit title",
                description="The branch is never taken.",
                cause="Off by one.",
                fix="Compare with >=.",
                confidence="high",
            ),
        ),
        policy=PostingPolicy(),
    )
    body = build_sticky_comment(
        request=StickyRequest(
            result=replace(sample_review_result, findings=nit),
            head_sha="abc123def456",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
    )

    assert_that(body).contains(
        "| **new** | **Nit title**<br>The branch is never taken.<br>"
        "Fix: Compare with >=. | `src/app.py:9` <!-- lintro-finding:",
    )
