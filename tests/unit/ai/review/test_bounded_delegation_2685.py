"""Bounded CLI calls and the git-native prompt (#2685, review round 2).

Two claims the bounded read-only tool surface makes on the review pipeline:

* the working-tree note in the git-native user prompt must describe the mode
  the prompt is rendered in: a PR review reads a base-ref checkout, an
  uncommitted (``WORKTREE``) review reads the change itself;
* the delegated ``git diff`` opt-in cannot be honoured by an agent whose
  tools have no shell (Claude's ``--tools Read,Grep,Glob``), so an oversized
  chunk takes the embedded (redacted) path and the run records it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.chunk_split_retry import review_chunk_main_pass
from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.prompts import PromptInputs, build_git_native_review_prompt
from lintro.ai.review.response_pipeline import (
    ChunkReviewRequest,
    provider_can_run_commands,
)

_PR_NOTE = "any file you read from disk shows its pre-change content"
_WORKTREE_NOTE = "any file you read from disk shows its post-change content"
_UNKNOWN_NOTE = "Which side of the range the working tree holds was"
_DELEGATED = "Run this command in the repository root"


def _chunk_and_context(
    *,
    head_ref: str,
    checkout: ReviewCheckout = ReviewCheckout.UNKNOWN,
) -> tuple[ReviewChunk, ReviewContext]:
    """Return a one-file chunk and the context it belongs to.

    Args:
        head_ref: Head ref of the context, ``WORKTREE`` for an uncommitted review.
        checkout: Which side of the range the context says is on disk.

    Returns:
        The chunk and its review context.
    """
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n+x = 1\n"
    )
    changed = ChangedFile(path="src/a.py", status="modified", additions=1, deletions=0)
    context = ReviewContext(
        base_ref="abc123",
        head_ref=head_ref,
        changed_files=[changed],
        unified_diff=diff,
        pr_metadata=None,
        checkout=checkout,
    )
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py"],
        diff=diff,
        relationship="directory-prefix",
    )
    return chunk, context


def _inputs(
    *,
    head_ref: str,
    checkout: ReviewCheckout = ReviewCheckout.UNKNOWN,
) -> PromptInputs:
    chunk, context = _chunk_and_context(head_ref=head_ref, checkout=checkout)
    return PromptInputs(
        chunk=chunk,
        context=context,
        checklist_text="",
        checklist_count=0,
        interaction_paths="",
        lint_results=None,
        extra_checklist="",
        strictness_section="",
    )


def test_a_base_checkout_says_disk_is_pre_change() -> None:
    """A CI PR review runs on a base-ref checkout, so disk reads are pre-change."""
    _, prompt = build_git_native_review_prompt(
        inputs=_inputs(head_ref="feature/x", checkout=ReviewCheckout.BASE),
        embed_diff=True,
    )
    assert_that(prompt).contains(_PR_NOTE, "base ref `abc123`")
    assert_that(prompt).does_not_contain(_WORKTREE_NOTE, _UNKNOWN_NOTE)


def test_a_head_checkout_says_disk_is_post_change() -> None:
    """A branch review runs on the branch itself, so disk is post-change."""
    _, prompt = build_git_native_review_prompt(
        inputs=_inputs(head_ref="feature/x", checkout=ReviewCheckout.HEAD),
        embed_diff=True,
    )
    assert_that(prompt).contains(_WORKTREE_NOTE, "base of the range is `abc123`")
    assert_that(prompt).does_not_contain(_PR_NOTE, _UNKNOWN_NOTE)


def test_worktree_mode_says_disk_is_post_change() -> None:
    """An uncommitted review reads the change itself, so disk is post-change."""
    _, prompt = build_git_native_review_prompt(
        inputs=_inputs(head_ref="WORKTREE", checkout=ReviewCheckout.WORKTREE),
        embed_diff=True,
    )
    assert_that(prompt).contains(_WORKTREE_NOTE)
    assert_that(prompt).does_not_contain(_PR_NOTE, _UNKNOWN_NOTE)
    # The sentinel alone is enough: an older context without ``checkout``.
    _, prompt = build_git_native_review_prompt(
        inputs=_inputs(head_ref="WORKTREE"),
        embed_diff=True,
    )
    assert_that(prompt).contains(_WORKTREE_NOTE)


def test_an_undetermined_checkout_tells_the_agent_to_check() -> None:
    """Neither claim is made when the checkout is unknown."""
    _, prompt = build_git_native_review_prompt(
        inputs=_inputs(head_ref="feature/x"),
        embed_diff=True,
    )
    assert_that(prompt).contains(_UNKNOWN_NOTE)
    assert_that(prompt).does_not_contain(_PR_NOTE, _WORKTREE_NOTE)


def test_only_claude_withholds_the_shell() -> None:
    """Codex and cursor keep a shell in their read-only modes; claude does not."""
    for name, expected in (("anthropic", False), ("openai", True), ("cursor", True)):
        provider = MagicMock()
        provider.name = name
        assert_that(provider_can_run_commands(provider)).is_equal_to(expected)
    unknown = MagicMock()
    unknown.name = "not-a-provider"
    assert_that(provider_can_run_commands(unknown)).is_true()


def _ok_response() -> AIResponse:
    return AIResponse(
        content='{"findings": [], "flagged_files": []}',
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.0,
    )


async def _main_pass(*, tmp_path: Path, provider_name: str) -> tuple[Any, list[str]]:
    """Run the main pass with the delegated opt-in on an oversized chunk.

    Args:
        tmp_path: Repository root stand-in.
        provider_name: Registered provider name the fake provider reports.

    Returns:
        The chunk partial and every user prompt sent.
    """
    chunk, context = _chunk_and_context(head_ref="feature/x")
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "m"
    provider.name = provider_name
    provider.capabilities.supports_sessions = False
    budget = MagicMock()
    budget.check = MagicMock()
    prompts: list[str] = []

    async def _fake_call_ai(**kwargs: object) -> AIResponse:
        prompts.append(str(kwargs.get("user_prompt", "")))
        return _ok_response()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(side_effect=_fake_call_ai),
    ):
        partial = await review_chunk_main_pass(
            request=ChunkReviewRequest(
                chunk=chunk,
                context=context,
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    transport=AITransport.CLI,
                    review_allow_unredacted_git_native=True,
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
                diff_budget=1,  # every chunk is oversized
                chunk_index=2,
            ),
        )
    return partial, prompts


async def test_claude_embeds_the_diff_instead_of_delegating(tmp_path: Path) -> None:
    """Opt-in + oversized chunk + shell-less tools: the redacted diff is embedded.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    partial, prompts = await _main_pass(tmp_path=tmp_path, provider_name="anthropic")

    assert_that(prompts).is_length(1)
    assert_that(prompts[0]).does_not_contain(_DELEGATED)
    assert_that(prompts[0]).contains("+x = 1")
    assert_that([item.reason for item in partial.coverage_degradations]).is_equal_to(
        [CoverageDegradationReason.DELEGATED_DIFF_EMBEDDED],
    )
    assert_that(partial.coverage_degradations[0].chunk_index).is_equal_to(2)


async def test_codex_still_delegates_the_diff(tmp_path: Path) -> None:
    """A provider whose read-only mode keeps a shell honours the opt-in.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    partial, prompts = await _main_pass(tmp_path=tmp_path, provider_name="openai")

    assert_that(prompts[0]).contains(_DELEGATED)
    assert_that(partial.coverage_degradations).is_empty()


def test_the_fallback_is_recorded_but_is_not_a_coverage_loss() -> None:
    """The reason reaches the run record without marking coverage incomplete."""
    assert_that(NARRATIVE_DEGRADATION_REASONS).contains(
        CoverageDegradationReason.DELEGATED_DIFF_EMBEDDED,
    )
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
        token_usage={},
        cost_estimate_usd=0.0,
        base_ref="a",
        head_ref="b",
        timestamp="t",
        strictness="balanced",
        coverage_degradations=(
            CoverageDegradation(
                reason=CoverageDegradationReason.DELEGATED_DIFF_EMBEDDED,
                chunk_index=0,
                split=False,
            ),
        ),
    )
    assert_that(metadata.findings_coverage_complete).is_true()
    assert_that(metadata.synthesis_degraded).is_false()
    assert_that(metadata.delegated_diff_embedded).is_true()

    record = RunRecord(coverage=RunCoverage(delegated_diff_embedded=True))
    assert_that(record.to_dict()).contains_entry({"delegated_diff_embedded": True})
    assert_that(
        RunRecord.from_dict(record.to_dict()).coverage.delegated_diff_embedded,
    ).is_true()
    # Unset stays absent, so older records round-trip byte-identically.
    assert_that(RunRecord().to_dict()).does_not_contain_key("delegated_diff_embedded")
