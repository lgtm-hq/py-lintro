"""Tests for the cross-chunk synthesis pass (issue #2269).

The pass is one extra provider call made after the chunk findings are merged,
asked only for inconsistencies between files reviewed in different chunks. It
ships off by default, so most of what these tests assert is that a default run
is byte-identical to one from before the pass existed, and that an enabled run
degrades rather than fails when the pass cannot do its job.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from pydantic import ValidationError
from rich.console import Console

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError, AIError
from lintro.ai.prompts.review import (
    REVIEW_SYNTHESIS_SYSTEM_PROMPT,
    REVIEW_SYSTEM,
    format_changed_files_for_prompt,
    format_chunk_summaries_for_prompt,
)
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import fingerprint_for, match_findings
from lintro.ai.review.github_notes import format_synthesis_note_line
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.orchestrator import guard_changed_paths, run_review
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky import build_sticky_comment
from lintro.ai.review.synthesis import SynthesisPassRequest, run_synthesis_pass
from lintro.ai.review.synthesis_prompt import (
    build_synthesis_prompt,
    cross_chunk_paths,
    guarded_changed_paths,
    plan_synthesis_prompt,
    select_synthesis_diff,
)
from lintro.ai.review.timings import ReviewPhase
from lintro.ai.token_budget import estimate_tokens
from lintro.config.review_config import ReviewConfig, ReviewSynthesisConfig

_SIGNATURE_DIFF = (
    "diff --git a/pkg/api.py b/pkg/api.py\n"
    "--- a/pkg/api.py\n+++ b/pkg/api.py\n"
    "@@ -1,2 +1,2 @@\n-def send(payload):\n+def send(payload, *, retries):\n"
)
_CALLER_DIFF = (
    "diff --git a/pkg/caller.py b/pkg/caller.py\n"
    "--- a/pkg/caller.py\n+++ b/pkg/caller.py\n"
    "@@ -1,2 +1,2 @@\n-send(body)\n+send(body, 3)\n"
)
_MIGRATE_DIFF = (
    "diff --git a/scripts/migrate_docs.py b/scripts/migrate_docs.py\n"
    "--- a/scripts/migrate_docs.py\n+++ b/scripts/migrate_docs.py\n"
    "@@ -1,2 +1,2 @@\n-def migrate(path):\n+def migrate(path, *, dry_run):\n"
)
_MIGRATE_TEST_DIFF = (
    "diff --git a/tests/unit/test_migrate_docs.py b/tests/unit/test_migrate_docs.py\n"
    "--- a/tests/unit/test_migrate_docs.py\n"
    "+++ b/tests/unit/test_migrate_docs.py\n"
    "@@ -1,2 +1,2 @@\n-migrate(path)\n+migrate(path, dry_run=True)\n"
)


def _pr_context() -> ReviewContext:
    """Build a two-file PR context with a signature change and a caller.

    Returns:
        A review context whose diff carries both halves of one cross-file bug.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="pkg/api.py", status="modified", additions=1, deletions=1),
            ChangedFile(
                path="pkg/caller.py",
                status="modified",
                additions=1,
                deletions=1,
            ),
        ],
        unified_diff=_SIGNATURE_DIFF + _CALLER_DIFF,
        pr_metadata=None,
    )


def _two_chunks() -> list[ReviewChunk]:
    """Build the two-chunk plan that splits the bug's halves apart.

    Returns:
        Two single-file chunks, one per half of the cross-file bug.
    """
    return [
        ReviewChunk(
            id=1,
            files=["pkg/api.py"],
            diff=_SIGNATURE_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["pkg/caller.py"],
            diff=_CALLER_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    ]


def _chunk_payload() -> str:
    """Build a chunk response reporting nothing cross-file.

    Returns:
        JSON text for a clean chunk pass.
    """
    return json.dumps(
        {"summary": "Looks fine in isolation.", "checklist": [], "findings": []},
    )


def _synthesis_payload(
    *,
    findings: list[dict[str, Any]] | None = None,
) -> str:
    """Build a synthesis response payload.

    Args:
        findings: Raw finding mappings to return. Defaults to the one
            cross-file finding the fixture PR should produce.

    Returns:
        JSON text for the synthesis call.
    """
    if findings is None:
        findings = [
            {
                "severity": "P1",
                "category": "logic-bug",
                "file": "pkg/caller.py",
                "line": 1,
                "title": "Caller passes retries positionally",
                "description": (
                    "pkg/api.py made retries keyword-only; pkg/caller.py "
                    "still passes it positionally."
                ),
                "cause": "Signature change in pkg/api.py",
                "fix": "Call send(body, retries=3)",
                "failure_scenario": "Every call raises TypeError at runtime",
                "confidence": "high",
            },
        ]
    return json.dumps({"findings": findings})


def _mock_provider() -> MagicMock:
    """Build a provider double with explicit, session-free capabilities.

    Returns:
        A provider mock safe for the multi-chunk fan-out path.
    """
    provider = MagicMock()
    # The run session closes every provider it owns (#2302), so the
    # double has to model an awaitable ``aclose``.
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


#: Usage counters every mocked provider call reports. Named so a test can
#: assert what one extra call adds without repeating the arithmetic.
_RESPONSE_INPUT_TOKENS = 100
_RESPONSE_OUTPUT_TOKENS = 50


def _response(*, content: str) -> AIResponse:
    """Wrap raw text as a provider response.

    Args:
        content: Response body.

    Returns:
        An ``AIResponse`` with fixed, deterministic usage counters.
    """
    return AIResponse(
        content=content,
        model="claude-sonnet-4-20250514",
        input_tokens=_RESPONSE_INPUT_TOKENS,
        output_tokens=_RESPONSE_OUTPUT_TOKENS,
        cost_estimate=0.01,
        provider="anthropic",
    )


def _run(
    *,
    synthesis: ReviewSynthesisConfig | None,
    context: ReviewContext | None = None,
    chunks: list[ReviewChunk] | None = None,
    synthesis_content: str | None = None,
    synthesis_error: Exception | None = None,
    chunk_calls: list[str] | None = None,
    synthesis_calls: list[str] | None = None,
    strictness: ReviewStrictness | None = None,
    synthesis_diff_budget: int | None = None,
    synthesis_system_prompts: list[str] | None = None,
) -> Any:
    """Run a review with the chunk and synthesis provider calls mocked apart.

    The two calls are patched at different import sites, so the tests can
    assert exactly how many of each the run made.

    Args:
        synthesis: Synthesis configuration passed to ``run_review``.
        context: Review context to review. Defaults to the two-file fixture PR.
        chunks: Chunk plan to force. Defaults to the two-chunk fixture.
        synthesis_content: Raw text the synthesis call returns.
        synthesis_error: Exception the synthesis call raises instead.
        chunk_calls: Optional sink recording each chunk prompt.
        synthesis_calls: Optional sink recording each synthesis prompt.
        strictness: Optional sensitivity preset for the whole run.
        synthesis_diff_budget: Optional token budget forced onto the synthesis
            prompt planner, so a fixture PR small enough to fit any real
            budget can still exercise the truncation path end to end.
        synthesis_system_prompts: Optional sink recording the system prompt
            each synthesis call was made with.

    Returns:
        The review result.
    """
    provider = _mock_provider()
    chunk_sink = chunk_calls if chunk_calls is not None else []
    synthesis_sink = synthesis_calls if synthesis_calls is not None else []
    synthesis_system_sink = (
        synthesis_system_prompts if synthesis_system_prompts is not None else []
    )

    async def _chunk_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        chunk_sink.append(user_prompt)
        return _response(content=_chunk_payload())

    async def _synthesis_call(
        *,
        user_prompt: str,
        system_prompt: str | None = None,
        **_kwargs: Any,
    ) -> AIResponse:
        synthesis_sink.append(user_prompt)
        synthesis_system_sink.append(system_prompt or "")
        if synthesis_error is not None:
            raise synthesis_error
        return _response(content=synthesis_content or _synthesis_payload())

    def _forced_plan(*, context: Any, summaries: Any, diff_budget: int) -> Any:
        return plan_synthesis_prompt(
            context=context,
            summaries=summaries,
            diff_budget=synthesis_diff_budget or diff_budget,
        )

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "lintro.ai.review.run_planning.resolve_review_chunks",
                return_value=chunks if chunks is not None else _two_chunks(),
            ),
        )
        stack.enter_context(
            patch(
                "lintro.ai.review.provider_call.call_ai",
                side_effect=_chunk_call,
            ),
        )
        stack.enter_context(
            patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
        )
        if synthesis_diff_budget is not None:
            stack.enter_context(
                patch(
                    "lintro.ai.review.synthesis.plan_synthesis_prompt",
                    side_effect=_forced_plan,
                ),
            )
        return run_review(
            context if context is not None else _pr_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                sensitivity=(
                    None
                    if strictness is None
                    else resolve_sensitivity_policy(strictness=strictness)
                ),
                synthesis=synthesis,
            ),
        )


def _outcome(*, result: Any) -> SynthesisOutcome:
    """Return the run's synthesis outcome, failing the test when absent.

    Args:
        result: Review result to read.

    Returns:
        The recorded synthesis outcome.
    """
    outcome: SynthesisOutcome | None = result.metadata.synthesis
    if outcome is None:
        pytest.fail("the synthesis pass did not record an outcome")
    return outcome


# --- (a) default: the pass never runs, and nothing changes ---------------------


def test_default_config_makes_no_extra_call() -> None:
    """A run with no synthesis config makes exactly one call per chunk."""
    synthesis_calls: list[str] = []
    chunk_calls: list[str] = []

    result = _run(
        synthesis=None,
        chunk_calls=chunk_calls,
        synthesis_calls=synthesis_calls,
    )

    assert_that(chunk_calls).is_length(2)
    assert_that(synthesis_calls).is_empty()
    assert_that(result.metadata.synthesis).is_none()


def test_default_config_leaves_the_json_payload_unchanged() -> None:
    """No ``synthesis`` key and no ``origin`` field on a default run."""
    result = _run(synthesis=None)

    payload = review_result_to_dict(result=result)

    assert_that(payload).does_not_contain_key("synthesis")
    assert_that(payload["metadata"]).does_not_contain_key("synthesis")
    for finding in payload["findings"]:
        assert_that(finding).does_not_contain_key("origin")


def test_default_config_renders_no_note_on_any_surface() -> None:
    """Terminal and GitHub surfaces stay silent when the pass did not run."""
    result = _run(synthesis=None)

    console = Console(record=True)
    render_review_terminal(result=result, console=console)

    assert_that(console.export_text()).does_not_contain("Cross-chunk synthesis")
    assert_that(format_synthesis_note_line(metadata=result.metadata)).is_empty()


def _github_surfaces(*, result: Any) -> tuple[str, str]:
    """Render both posted GitHub surfaces for one result.

    Args:
        result: Review result to render.

    Returns:
        Tuple of ``(review_body, sticky_body)``.
    """
    prior_state = ReviewState()
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha="deadbeef",
    )
    body = build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha="deadbeef",
    )
    sticky = build_sticky_comment(
        request=StickyRequest(result=result, head_sha="deadbeef"),
    )
    return body, sticky


def test_default_config_leaves_the_github_surfaces_unchanged() -> None:
    """Neither posted surface mentions a pass that never ran."""
    body, sticky = _github_surfaces(result=_run(synthesis=None))

    assert_that(body).does_not_contain("Cross-chunk synthesis")
    assert_that(sticky).does_not_contain("Cross-chunk synthesis")


def test_enabled_run_notes_the_pass_on_both_github_surfaces() -> None:
    """The review body and the sticky carry the same shared note."""
    result = _run(synthesis=ReviewSynthesisConfig(enabled=True))

    body, sticky = _github_surfaces(result=result)

    assert_that(body).contains("Cross-chunk synthesis added 1 cross-file finding")
    assert_that(sticky).contains("Cross-chunk synthesis added 1 cross-file finding")


def test_disabled_config_makes_no_extra_call() -> None:
    """An explicitly disabled config is the same as no config at all."""
    synthesis_calls: list[str] = []

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=False),
        synthesis_calls=synthesis_calls,
    )

    assert_that(synthesis_calls).is_empty()
    assert_that(result.metadata.synthesis).is_none()


# --- (b) enabled but only one chunk ------------------------------------------


def test_enabled_with_one_chunk_makes_no_extra_call() -> None:
    """A single-chunk run has no chunk boundary to reason across."""
    synthesis_calls: list[str] = []
    one_chunk = [
        ReviewChunk(
            id=1,
            files=["pkg/api.py", "pkg/caller.py"],
            diff=_SIGNATURE_DIFF + _CALLER_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    ]

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        chunks=one_chunk,
        synthesis_calls=synthesis_calls,
    )

    assert_that(synthesis_calls).is_empty()
    assert_that(result.metadata.synthesis).is_none()


# --- (c) the fixture PR the issue names --------------------------------------


def test_cross_file_finding_surfaces_tagged_and_counted() -> None:
    """The split signature/caller bug is found, tagged, counted, and noted."""
    synthesis_calls: list[str] = []

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_calls=synthesis_calls,
    )

    assert_that(synthesis_calls).is_length(1)
    assert_that(result.findings).is_length(1)
    finding = result.findings[0]
    assert_that(finding.title).contains("retries positionally")
    assert_that(finding.origin).is_equal_to(FindingOrigin.SYNTHESIS)
    # An evidenced P1 from this pass blocks the run like any other P1: the
    # findings are merged before the result is assembled, so the derived
    # verdict sees them.
    assert_that(finding.severity).is_equal_to(Severity.P1)
    assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.BLOCKED)

    payload = review_result_to_dict(result=result)
    synthesis_block = payload["synthesis"]
    assert_that(synthesis_block["enabled"]).is_true()
    assert_that(synthesis_block["findings_added"]).is_equal_to(1)
    assert_that(synthesis_block["truncated"]).is_false()
    assert_that(synthesis_block["failed"]).is_false()
    assert_that(payload["findings"][0]["origin"]).is_equal_to("synthesis")

    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("added 1 cross-file finding")
    console = Console(record=True)
    render_review_terminal(result=result, console=console)
    assert_that(console.export_text()).contains("Cross-chunk synthesis")


def test_synthesis_records_its_own_phase_span() -> None:
    """Cost and wall-clock land on the existing #2148 timing surfaces."""
    baseline = _run(synthesis=None)
    result = _run(synthesis=ReviewSynthesisConfig(enabled=True))

    timings = result.metadata.timings
    assert_that(timings).is_not_none()
    names = [span.name for span in timings.phases]
    assert_that(names).contains(ReviewPhase.SYNTHESIS.value)
    assert_that(
        [span.name for span in baseline.metadata.timings.phases],
    ).does_not_contain(
        ReviewPhase.SYNTHESIS.value,
    )
    # The pass is exactly one extra call, so the run's totals grow by exactly
    # what that call reported — never by a literal copied from the fixture.
    assert_that(result.metadata.token_usage["total"]).is_equal_to(
        baseline.metadata.token_usage["total"]
        + _RESPONSE_INPUT_TOKENS
        + _RESPONSE_OUTPUT_TOKENS,
    )


def test_synthesis_prompt_names_every_changed_file_and_each_chunk() -> None:
    """The pass sees the whole-PR file list, not one chunk's slice."""
    synthesis_calls: list[str] = []

    _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_calls=synthesis_calls,
    )

    prompt = synthesis_calls[0]
    assert_that(prompt).contains("pkg/api.py")
    assert_that(prompt).contains("pkg/caller.py")
    assert_that(prompt).contains("Piece 1 reviewed")
    assert_that(prompt).contains("Piece 2 reviewed")


# --- (d) the #1914-style phantom ---------------------------------------------


def test_phantom_without_a_failure_mechanism_is_downgraded_not_blocking() -> None:
    """An unevidenced P1 phantom comes back as a marked, non-blocking P2."""
    phantom = _synthesis_payload(
        findings=[
            {
                "severity": "P1",
                "category": "logic-bug",
                "file": "pkg/caller.py",
                "line": 1,
                "title": "The other file was never updated",
                "description": "pkg/api.py appears untouched.",
                "cause": "unknown",
                "fix": "Update pkg/api.py",
                "confidence": "low",
            },
        ],
    )

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=phantom,
    )

    assert_that(result.findings).is_length(1)
    finding = result.findings[0]
    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.severity_downgraded).is_true()
    assert_that(result.readiness_verdict).is_not_equal_to(ReviewVerdict.BLOCKED)


def _migrate_docs_context() -> ReviewContext:
    """Build the #1914 fixture PR: a script and its test, both changed.

    Returns:
        A review context whose changed set holds both
        ``scripts/migrate_docs.py`` and ``tests/unit/test_migrate_docs.py``,
        so a claim that the test "was never updated" contradicts the diff.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="scripts/migrate_docs.py",
                status="modified",
                additions=1,
                deletions=1,
            ),
            ChangedFile(
                path="tests/unit/test_migrate_docs.py",
                status="modified",
                additions=1,
                deletions=1,
            ),
        ],
        unified_diff=_MIGRATE_DIFF + _MIGRATE_TEST_DIFF,
        pr_metadata=None,
    )


def _migrate_docs_chunks() -> list[ReviewChunk]:
    """Split the #1914 fixture PR so neither half can see the other.

    Returns:
        Two single-file chunks, one for the script and one for its test.
    """
    return [
        ReviewChunk(
            id=1,
            files=["scripts/migrate_docs.py"],
            diff=_MIGRATE_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["tests/unit/test_migrate_docs.py"],
            diff=_MIGRATE_TEST_DIFF,
            relationship=REL_SINGLE_FILE,
        ),
    ]


def _migrate_docs_phantom() -> str:
    """Build the #1914 phantom: an unchanged-file claim with a mechanism.

    The fabricated ``failure_scenario`` is what carries it past the P1
    evidence gate, so only the cross-chunk guard can stop it blocking.

    Returns:
        JSON text for a synthesis call returning one phantom P1.
    """
    return _synthesis_payload(
        findings=[
            {
                "severity": "P1",
                "category": "test-coverage",
                "file": "scripts/migrate_docs.py",
                "line": 1,
                "title": "The dry-run path ships with no test",
                "description": ("tests/unit/test_migrate_docs.py was never updated."),
                "cause": "The new keyword-only parameter has no coverage",
                "fix": "Add a dry-run case to tests/unit/test_migrate_docs.py",
                "failure_scenario": (
                    "A regression in the dry-run branch ships unnoticed and "
                    "deletes the source files on the next release run."
                ),
                "confidence": "high",
            },
        ],
    )


def test_phantom_with_a_fabricated_mechanism_is_guarded_one_band_lower() -> None:
    """A phantom past the evidence gate is still tagged and dropped a band."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        context=_migrate_docs_context(),
        chunks=_migrate_docs_chunks(),
        synthesis_content=_migrate_docs_phantom(),
    )

    assert_that(result.findings).is_length(1)
    finding = result.findings[0]
    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.cross_chunk_contradiction).is_not_none()
    assert_that(finding.origin).is_equal_to(FindingOrigin.SYNTHESIS)
    assert_that(result.readiness_verdict).is_not_equal_to(ReviewVerdict.BLOCKED)


def test_guarded_synthesized_finding_is_counted_and_still_attributed() -> None:
    """The guard's count reaches the JSON payload without losing the origin."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        context=_migrate_docs_context(),
        chunks=_migrate_docs_chunks(),
        synthesis_content=_migrate_docs_phantom(),
    )

    payload = review_result_to_dict(result=result)
    assert_that(payload["cross_chunk_contradictions"]).is_equal_to(1)
    assert_that(payload["findings"][0]["origin"]).is_equal_to("synthesis")
    assert_that(payload["findings"][0]["cross_chunk_contradiction"]).is_not_none()
    assert_that(payload["synthesis"]["findings_added"]).is_equal_to(1)


def test_guarded_changed_paths_matches_the_orchestrator_helper() -> None:
    """The pass's local path list is the guard's list, renames and copies."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="pkg/renamed.py",
                status="renamed",
                additions=1,
                deletions=1,
                previous_path="pkg/old_name.py",
            ),
            ChangedFile(
                path="pkg/copy.py",
                status="copied",
                additions=1,
                deletions=0,
                previous_path="pkg/source.py",
            ),
            ChangedFile(
                path="pkg/plain.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=_SIGNATURE_DIFF,
        pr_metadata=None,
    )

    paths = guarded_changed_paths(context=context)

    # The orchestrator name is a re-export, so this pins the delegation.
    assert_that(paths).is_equal_to(guard_changed_paths(context=context))
    # Asserted against the fixture rather than against the other helper, so
    # both dropping a rename or copy source cannot pass by agreeing.
    assert_that(paths).is_equal_to(
        (
            "pkg/renamed.py",
            "pkg/old_name.py",
            "pkg/copy.py",
            "pkg/source.py",
            "pkg/plain.py",
        ),
    )
    assert_that(paths).contains("pkg/old_name.py")
    assert_that(paths).contains("pkg/source.py")


# --- (e) cap and dedupe -------------------------------------------------------


def _numbered_findings(*, count: int) -> list[dict[str, Any]]:
    """Build distinct synthesized findings for the cap test.

    Args:
        count: How many findings to build.

    Returns:
        Raw finding mappings with unique titles.
    """
    return [
        {
            "severity": "P2",
            "category": "logic-bug",
            "file": "pkg/caller.py",
            "line": index + 1,
            "title": f"Cross-file mismatch {index}",
            "description": "pkg/api.py disagrees with pkg/caller.py.",
            "cause": "signature drift",
            "fix": "align them",
            "confidence": "medium",
        }
        for index in range(count)
    ]


def test_synthesized_findings_are_capped_at_max_findings() -> None:
    """The pass may never add more than its configured ceiling."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True, max_findings=2),
        synthesis_content=_synthesis_payload(findings=_numbered_findings(count=6)),
    )

    assert_that(result.findings).is_length(2)
    assert_that(_outcome(result=result).findings_added).is_equal_to(2)


def test_synthesized_duplicate_of_a_chunk_finding_is_dropped() -> None:
    """A synthesized restatement of a chunk finding never appears twice."""
    duplicate = {
        "severity": "P2",
        "category": "logic-bug",
        "file": "pkg/api.py",
        "line": 2,
        "title": "Known chunk issue",
        "description": "restated",
        "cause": "restated",
        "fix": "restated",
        "confidence": "medium",
    }
    chunk_payload = json.dumps(
        {
            "summary": "One issue.",
            "checklist": [],
            "findings": [
                {
                    "severity": "P2",
                    "category": "logic-bug",
                    "file": "pkg/api.py",
                    "line": 1,
                    "title": "Known chunk issue",
                    "description": "reported by the chunk",
                    "cause": "c",
                    "fix": "f",
                    "confidence": "medium",
                },
            ],
        },
    )
    provider = _mock_provider()

    async def _chunk_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        return _response(content=chunk_payload)

    async def _synthesis_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        return _response(content=_synthesis_payload(findings=[duplicate]))

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_two_chunks(),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_chunk_call),
        patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
    ):
        result = run_review(
            _pr_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                synthesis=ReviewSynthesisConfig(enabled=True),
            ),
        )

    assert_that(_outcome(result=result).findings_added).is_equal_to(0)
    assert_that(
        [finding.origin for finding in result.findings],
    ).does_not_contain(FindingOrigin.SYNTHESIS)


# --- (f) truncation and failure degrade, never fail ---------------------------


def test_provider_failure_degrades_the_run_instead_of_ending_it() -> None:
    """A failed synthesis call keeps the chunk findings and marks coverage."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_error=AIError("provider exploded"),
    )

    assert_that(result.metadata.partial).is_false()
    assert_that(_outcome(result=result).failed).is_true()
    assert_that(result.metadata.findings_coverage_complete).is_false()
    reasons = [item.reason for item in result.metadata.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SYNTHESIS_FAILED)


def test_unparseable_response_degrades_the_run_instead_of_ending_it() -> None:
    """A non-JSON answer is a failed pass, not an empty one."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content="I could not comply.",
    )

    assert_that(_outcome(result=result).failed).is_true()
    assert_that(_outcome(result=result).findings_added).is_equal_to(0)
    assert_that(result.metadata.findings_coverage_complete).is_false()
    reasons = [item.reason for item in result.metadata.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SYNTHESIS_FAILED)
    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("did not complete")


def test_a_findings_cap_is_never_reported_for_a_synthesis_degradation() -> None:
    """Synthesis reasons carry a placeholder cap and stay out of the ceiling."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_error=AIError("provider exploded"),
    )

    assert_that(result.metadata.findings_cap_applied).is_none()


def test_select_synthesis_diff_sends_the_whole_pr_when_it_fits() -> None:
    """An in-budget PR reaches the pass whole and unmarked."""
    context = _pr_context()

    diff, truncated = select_synthesis_diff(
        context=context,
        summaries=(),
        diff_budget=100_000,
    )

    assert_that(diff).is_equal_to(context.unified_diff)
    assert_that(truncated).is_false()


def test_select_synthesis_diff_keeps_shared_files_first_when_over_budget() -> None:
    """The files two chunks referenced survive the cut; the rest may not."""
    context = _pr_context()
    summaries = (
        ChunkSummary(
            chunk_id=1,
            files=("pkg/api.py",),
            findings=(
                ReviewFinding(
                    severity=Severity.P2,
                    category="logic-bug",
                    file="pkg/caller.py",
                    line=1,
                    title="points at the other chunk",
                    description="",
                    cause="",
                    fix="",
                    confidence="medium",
                ),
            ),
        ),
        ChunkSummary(chunk_id=2, files=("pkg/caller.py",), findings=()),
    )

    assert_that(list(cross_chunk_paths(summaries=summaries))).is_equal_to(
        ["pkg/caller.py"],
    )

    diff, truncated = select_synthesis_diff(
        context=context,
        summaries=summaries,
        diff_budget=estimate_tokens(_CALLER_DIFF) + 1,
    )

    assert_that(truncated).is_true()
    assert_that(diff).contains("pkg/caller.py")


def test_a_cut_input_is_declared_to_the_model_in_the_prompt() -> None:
    """A cut input warns the model that the diff it was given is partial.

    Scope is the prompt text only. That a cut input also degrades the run's
    recorded coverage is asserted end to end by
    :func:`test_a_truncated_pass_degrades_coverage_end_to_end`.
    """
    context = _pr_context()
    plan = plan_synthesis_prompt(
        context=context,
        summaries=(),
        diff_budget=estimate_tokens(_SIGNATURE_DIFF) + 1,
    )
    _system, user_prompt = build_synthesis_prompt(
        context=context,
        plan=plan,
        max_findings=5,
    )

    assert_that(plan.truncated).is_true()
    assert_that(user_prompt).contains("only part of this PR")


# --- (g) config validation ----------------------------------------------------


def test_synthesis_is_disabled_by_default() -> None:
    """The pass ships off pending the #2147 cost measurement."""
    assert_that(ReviewConfig().synthesis.enabled).is_false()
    assert_that(ReviewConfig().synthesis.max_findings).is_equal_to(5)


def test_max_findings_rejects_a_boolean() -> None:
    """``true`` must not validate as a silent cap of one."""
    with pytest.raises(ValidationError):
        ReviewSynthesisConfig(max_findings=True)


@pytest.mark.parametrize("value", [0, -1])
def test_max_findings_rejects_values_below_one(value: int) -> None:
    """A cap the pass could never satisfy is a configuration error.

    Args:
        value: Rejected ``max_findings`` value.
    """
    with pytest.raises(ValidationError):
        ReviewSynthesisConfig(max_findings=value)


def test_unknown_synthesis_key_is_rejected() -> None:
    """The sub-config forbids extras like every sibling review section."""
    with pytest.raises(ValidationError):
        ReviewSynthesisConfig.model_validate({"enabled": True, "unknown": 1})


# --- (h) package exports ------------------------------------------------------


def test_new_value_objects_construct_and_serialize_from_the_package() -> None:
    """The new value objects are usable through the package namespaces."""
    from lintro.ai.review import enums, models

    summary = models.ChunkSummary(
        chunk_id=1,
        files=("pkg/api.py",),
        findings=(),
    )
    outcome = models.SynthesisOutcome(findings_added=2, truncated=True, failed=False)

    assert_that(summary.files).is_equal_to(("pkg/api.py",))
    assert_that(outcome.to_dict()).is_equal_to(
        {
            "enabled": True,
            "findings_added": 2,
            "truncated": True,
            "failed": False,
        },
    )
    assert_that(str(enums.FindingOrigin.SYNTHESIS)).is_equal_to("synthesis")


# --- (i) the whole prompt is budgeted, not only the diff ----------------------


def _sized_diff(*, path: str, lines: int) -> str:
    """Build a per-file diff section of a controlled size.

    Args:
        path: Repository-relative path the section is for.
        lines: How many added lines the hunk carries.

    Returns:
        A unified-diff section for one file.
    """
    body = "".join(f"+line {index} in {path}\n" for index in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{lines} @@\n{body}"
    )


def _wide_summaries(
    *,
    chunks: int,
    findings_per_chunk: int,
) -> tuple[ChunkSummary, ...]:
    """Build a digest big enough to matter against a prompt budget.

    Args:
        chunks: How many chunk digests to build.
        findings_per_chunk: How many already-reported findings each carries.

    Returns:
        Per-chunk digests in chunk order.
    """
    return tuple(
        ChunkSummary(
            chunk_id=chunk_id,
            files=(f"pkg/module_{chunk_id}.py",),
            findings=tuple(
                ReviewFinding(
                    severity=Severity.P2,
                    category="logic-bug",
                    file=f"pkg/module_{chunk_id}.py",
                    line=index + 1,
                    title=f"Reported issue {chunk_id}.{index} in a long title",
                    description="",
                    cause="",
                    fix="",
                    confidence="medium",
                )
                for index in range(findings_per_chunk)
            ),
        )
        for chunk_id in range(1, chunks + 1)
    )


def test_the_digest_is_charged_against_the_prompt_budget() -> None:
    """A large digest shrinks the diff instead of overrunning the budget."""
    context = _pr_context()
    summaries = _wide_summaries(chunks=6, findings_per_chunk=3)
    digest = format_chunk_summaries_for_prompt(summaries=summaries)
    changed_files = format_changed_files_for_prompt(files=list(context.changed_files))
    # Room for the digest and the file list, and almost nothing else — the
    # whole PR diff would fit this budget on its own.
    budget = estimate_tokens(digest) + estimate_tokens(changed_files) + 2

    unbudgeted, unbudgeted_truncated = select_synthesis_diff(
        context=context,
        summaries=summaries,
        diff_budget=budget,
    )
    plan = plan_synthesis_prompt(
        context=context,
        summaries=summaries,
        diff_budget=budget,
    )
    _system, user_prompt = build_synthesis_prompt(
        context=context,
        plan=plan,
        max_findings=5,
    )

    # The diff alone fits this budget; the whole prompt does not.
    assert_that(unbudgeted_truncated).is_false()
    assert_that(unbudgeted).is_equal_to(context.unified_diff)
    assert_that(plan.truncated).is_true()
    assert_that(len(plan.diff)).is_less_than(len(context.unified_diff))
    assert_that(plan.chunk_digest).is_equal_to(digest)
    assert_that(user_prompt).contains("Piece 6 reviewed")


def test_a_digest_over_the_whole_budget_sheds_its_finding_lines() -> None:
    """The widest chunk loses its reported-finding lines first."""
    context = _pr_context()
    wide = _wide_summaries(chunks=2, findings_per_chunk=8)
    summaries = (wide[0], replace(wide[1], findings=wide[1].findings[:1]))
    # Exactly enough for the digest that remains once the widest chunk's
    # finding lines are gone, and not a token more.
    after_shedding = format_chunk_summaries_for_prompt(
        summaries=(replace(summaries[0], findings=()), summaries[1]),
    )
    changed_files = format_changed_files_for_prompt(files=list(context.changed_files))
    budget = estimate_tokens(changed_files) + estimate_tokens(after_shedding)

    plan = plan_synthesis_prompt(
        context=context,
        summaries=summaries,
        diff_budget=budget,
    )

    assert_that(plan.truncated).is_true()
    assert_that(plan.chunk_digest).is_equal_to(after_shedding)
    # The chunk file lines are the last thing to go, and the narrower chunk
    # keeps its findings.
    assert_that(plan.chunk_digest).contains("Piece 1 reviewed")
    assert_that(plan.chunk_digest).contains("Piece 2 reviewed")
    assert_that(plan.chunk_digest).contains("Reported issue 2.0")
    assert_that(plan.chunk_digest).does_not_contain("Reported issue 1.7")


# --- (j) the diff selection stops at the first file that does not fit ---------


def _three_file_context(*, big_lines: int) -> ReviewContext:
    """Build a PR whose middle file is too big for a tight budget.

    Args:
        big_lines: How many lines the oversized file's hunk carries.

    Returns:
        A three-file review context.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="pkg/a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="pkg/b.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="pkg/z.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=(
            _sized_diff(path="pkg/a.py", lines=2)
            + _sized_diff(path="pkg/b.py", lines=big_lines)
            + _sized_diff(path="pkg/z.py", lines=1)
        ),
        pr_metadata=None,
    )


def _summaries_for(*, paths: tuple[str, ...]) -> tuple[ChunkSummary, ...]:
    """Build two chunk digests that both reference every given path.

    Args:
        paths: Paths to make cross-chunk.

    Returns:
        Two digests, so every named path counts as referenced twice.
    """
    return (
        ChunkSummary(chunk_id=1, files=paths, findings=()),
        ChunkSummary(chunk_id=2, files=paths, findings=()),
    )


def test_a_priority_file_that_does_not_fit_is_cut_and_kept() -> None:
    """Half a seam is still the seam, and nothing may follow the cut."""
    context = _three_file_context(big_lines=40)
    summaries = _summaries_for(paths=("pkg/a.py", "pkg/b.py"))
    small = _sized_diff(path="pkg/a.py", lines=2)
    big = _sized_diff(path="pkg/b.py", lines=40)
    budget = estimate_tokens(small) + estimate_tokens(big) // 2

    diff, truncated = select_synthesis_diff(
        context=context,
        summaries=summaries,
        diff_budget=budget,
    )

    assert_that(truncated).is_true()
    assert_that(diff).contains("pkg/a.py")
    # The over-budget priority file is present, but only in part.
    assert_that(diff).contains("+line 0 in pkg/b.py")
    assert_that(diff).does_not_contain("+line 39 in pkg/b.py")
    # No lower-priority file follows a cut one.
    assert_that(diff).does_not_contain("pkg/z.py")


def test_a_non_priority_file_that_does_not_fit_ends_the_selection() -> None:
    """A dropped file stops the walk rather than letting a later one in."""
    context = _three_file_context(big_lines=40)
    small = _sized_diff(path="pkg/a.py", lines=2)

    diff, truncated = select_synthesis_diff(
        context=context,
        summaries=(),
        diff_budget=estimate_tokens(small) + 2,
    )

    assert_that(truncated).is_true()
    assert_that(diff).contains("pkg/a.py")
    assert_that(diff).does_not_contain("pkg/b.py")
    assert_that(diff).does_not_contain("pkg/z.py")


# --- (k) truncation reaches the run's recorded coverage -----------------------


def test_a_truncated_pass_degrades_coverage_end_to_end() -> None:
    """A pass that saw part of the diff says so on every derived surface."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_diff_budget=1,
    )

    reasons = [item.reason for item in result.metadata.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SYNTHESIS_TRUNCATED)
    assert_that(_outcome(result=result).truncated).is_true()
    assert_that(result.metadata.findings_coverage_complete).is_false()
    assert_that(result.metadata.partial).is_false()

    payload = review_result_to_dict(result=result)
    assert_that(payload["synthesis"]["truncated"]).is_true()
    assert_that(payload["synthesis"]["failed"]).is_false()
    assert_that(payload["findings_coverage_complete"]).is_false()
    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("less than its whole input")


# --- (l) the sensitivity policy applies to synthesized findings ---------------


def test_the_sensitivity_policy_can_drop_a_synthesized_finding() -> None:
    """A preset that drops a band drops it for this pass too."""
    # On a reviewed path, so only the sensitivity policy can be what drops it.
    doc_nit = _synthesis_payload(
        findings=[
            {
                "severity": "P3",
                "category": "contract-drift",
                "file": "pkg/caller.py",
                "line": 1,
                "title": "Comment still describes the old signature",
                "description": (
                    "pkg/api.py changed the signature; the docstring in "
                    "pkg/caller.py still spells the old one."
                ),
                "cause": "signature change",
                "fix": "update the docstring",
                "confidence": "medium",
            },
        ],
    )

    focused = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=doc_nit,
        strictness=ReviewStrictness.FOCUSED,
    )
    balanced = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=doc_nit,
        strictness=ReviewStrictness.BALANCED,
    )

    assert_that(_outcome(result=balanced).findings_added).is_equal_to(1)
    assert_that(_outcome(result=focused).findings_added).is_equal_to(0)
    assert_that(
        [finding.origin for finding in focused.findings],
    ).does_not_contain(FindingOrigin.SYNTHESIS)


# --- (m) origin provenance in the state blob and across rounds ---------------


def _record(*, origin: FindingOrigin | None) -> FindingRecord:
    """Build a tracked record for the origin codec tests.

    Args:
        origin: Provenance to stamp, or ``None`` for a chunk finding.

    Returns:
        An open record for ``pkg/caller.py``.
    """
    return FindingRecord(
        fingerprint=fingerprint_for(
            file="pkg/caller.py",
            category="logic-bug",
            title="Caller passes retries positionally",
        ),
        severity=Severity.P2,
        category="logic-bug",
        title="Caller passes retries positionally",
        file="pkg/caller.py",
        line=1,
        status=FindingStatus.OPEN,
        origin=origin,
    )


def _decoded(*, payload: dict[str, Any]) -> FindingRecord:
    """Decode a state-blob mapping, failing the test when it is unusable.

    Args:
        payload: One finding's mapping from the state blob.

    Returns:
        The parsed record.
    """
    record = FindingRecord.from_dict(payload)
    if record is None:
        pytest.fail("the record did not decode from its own serialized form")
    return record


def test_a_synthesis_origin_round_trips_through_the_state_blob() -> None:
    """The provenance a run recorded survives being persisted and reread."""
    record = _record(origin=FindingOrigin.SYNTHESIS)

    payload = record.to_dict()
    restored = _decoded(payload=payload)

    assert_that(payload["origin"]).is_equal_to("synthesis")
    assert_that(restored.origin).is_equal_to(FindingOrigin.SYNTHESIS)


def test_a_record_without_an_origin_stays_originless() -> None:
    """An ordinary chunk finding never gains a key or an origin."""
    record = _record(origin=None)

    payload = record.to_dict()
    restored = _decoded(payload=payload)

    assert_that(payload).does_not_contain_key("origin")
    assert_that(restored.origin).is_none()


def test_an_unrecognized_origin_label_decodes_to_none_and_is_dropped() -> None:
    """A label this version does not know degrades to a chunk finding."""
    payload = {**_record(origin=None).to_dict(), "origin": "from-the-future"}

    restored = _decoded(payload=payload)

    assert_that(restored.origin).is_none()
    assert_that(restored.to_dict()).does_not_contain_key("origin")


def test_a_synthesis_record_keeps_its_origin_when_a_chunk_reports_it_next() -> None:
    """Provenance belongs to the first sighting, not the latest one."""
    prior = ReviewState(findings=(_record(origin=FindingOrigin.SYNTHESIS),))
    chunk_finding = ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file="pkg/caller.py",
        line=3,
        title="Caller passes retries positionally",
        description="reported by a chunk this round",
        cause="c",
        fix="f",
        confidence="medium",
    )

    match = match_findings(previous=prior, findings=(chunk_finding,), round_number=2)

    carried = [record for record in match.records if record.file == "pkg/caller.py"]
    assert_that(carried).is_length(1)
    assert_that(carried[0].origin).is_equal_to(FindingOrigin.SYNTHESIS)


def test_a_chunk_record_is_not_re_attributed_by_a_later_synthesis_hit() -> None:
    """The reverse holds too: a chunk-first record stays originless."""
    prior = ReviewState(findings=(_record(origin=None),))
    synthesized = ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file="pkg/caller.py",
        line=3,
        title="Caller passes retries positionally",
        description="reported by the synthesis pass this round",
        cause="c",
        fix="f",
        confidence="medium",
        origin=FindingOrigin.SYNTHESIS,
    )

    match = match_findings(previous=prior, findings=(synthesized,), round_number=2)

    carried = [record for record in match.records if record.file == "pkg/caller.py"]
    assert_that(carried).is_length(1)
    assert_that(carried[0].origin).is_none()


# --- (n) the count the surfaces render is the count that survived -------------


def test_a_rejected_synthesized_finding_is_recounted_on_every_surface() -> None:
    """A finding dropped after the pass returned lowers the reported count.

    ``reject_context_findings`` and the cross-chunk guard both run after the
    pass reported its own tally, so a synthesized finding on a path this round
    was never asked to review is discarded downstream. The JSON block and the
    shared note must agree with what actually survived.
    """
    kept = {
        "severity": "P2",
        "category": "logic-bug",
        "file": "pkg/caller.py",
        "line": 1,
        "title": "Caller passes retries positionally",
        "description": "pkg/api.py made retries keyword-only.",
        "cause": "signature change",
        "fix": "use a keyword",
        "confidence": "high",
    }
    # Not a changed file, so the context-finding rejection discards it.
    rejected = {
        "severity": "P2",
        "category": "logic-bug",
        "file": "pkg/untouched.py",
        "line": 9,
        "title": "Unrelated module disagrees",
        "description": "pkg/api.py disagrees with pkg/untouched.py.",
        "cause": "signature change",
        "fix": "align them",
        "confidence": "medium",
    }

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=_synthesis_payload(findings=[kept, rejected]),
    )

    surviving = [
        finding
        for finding in result.findings
        if finding.origin is FindingOrigin.SYNTHESIS
    ]
    assert_that(surviving).is_length(1)
    assert_that(surviving[0].file).is_equal_to("pkg/caller.py")

    assert_that(_outcome(result=result).findings_added).is_equal_to(1)
    payload = review_result_to_dict(result=result)
    assert_that(payload["synthesis"]["findings_added"]).is_equal_to(1)
    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("added 1 cross-file finding")
    assert_that(note).does_not_contain("2 cross-file findings")


# --- (o) the pass makes its call with its own system prompt -------------------


def test_the_pass_calls_the_provider_with_the_synthesis_system_prompt() -> None:
    """The extra call carries the findings-only prompt, not the chunk one."""
    system_prompts: list[str] = []
    chunk_calls: list[str] = []

    _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        chunk_calls=chunk_calls,
        synthesis_system_prompts=system_prompts,
    )

    assert_that(system_prompts).is_length(1)
    assert_that(system_prompts[0]).is_equal_to(REVIEW_SYNTHESIS_SYSTEM_PROMPT)
    assert_that(system_prompts[0]).is_not_equal_to(REVIEW_SYSTEM)
    assert_that(system_prompts[0]).does_not_contain("Complete every checklist item")
    assert_that(system_prompts[0]).contains("empty `findings` array")


def test_the_terminal_note_reports_the_same_count_as_the_json_payload() -> None:
    """Every surface reads the recounted number, never the pass's own tally."""
    kept = {
        "severity": "P2",
        "category": "logic-bug",
        "file": "pkg/caller.py",
        "line": 1,
        "title": "Caller passes retries positionally",
        "description": "pkg/api.py made retries keyword-only.",
        "cause": "signature change",
        "fix": "use a keyword",
        "confidence": "high",
    }
    rejected = {**kept, "file": "pkg/untouched.py", "title": "Unrelated module drifted"}

    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=_synthesis_payload(findings=[kept, rejected]),
    )

    payload = review_result_to_dict(result=result)
    assert_that(payload["synthesis"]["findings_added"]).is_equal_to(1)
    console = Console(record=True)
    render_review_terminal(result=result, console=console)
    terminal = console.export_text()
    assert_that(terminal).contains("added 1 cross-file finding")
    assert_that(terminal).does_not_contain("added 2 cross-file findings")


# --- (p) dedupe runs before the cap -------------------------------------------


def test_restatements_never_consume_the_cap_window() -> None:
    """A novel cross-file finding survives a cap filled with restatements.

    Deduplicating after the cap would let two restatements of chunk findings
    eat a ``max_findings=2`` window and discard the one finding the pass
    actually exists to surface.
    """
    chunk_findings: list[dict[str, Any]] = [
        {
            "severity": "P2",
            "category": "logic-bug",
            "file": "pkg/api.py",
            "line": index + 1,
            "title": f"Known chunk issue {index}",
            "description": "reported by the chunk",
            "cause": "c",
            "fix": "f",
            "confidence": "medium",
        }
        for index in range(2)
    ]
    chunk_payload = json.dumps(
        {"summary": "Two issues.", "checklist": [], "findings": chunk_findings},
    )
    # The two restatements come first, so a cap applied before the dedupe
    # would consume the whole window on them.
    synthesized: list[dict[str, Any]] = [
        {**finding, "line": int(finding["line"]) + 10} for finding in chunk_findings
    ] + [
        {
            "severity": "P2",
            "category": "logic-bug",
            "file": "pkg/caller.py",
            "line": 1,
            "title": "Novel cross-file mismatch",
            "description": "pkg/api.py disagrees with pkg/caller.py.",
            "cause": "signature drift",
            "fix": "align them",
            "confidence": "high",
        },
    ]
    provider = _mock_provider()

    async def _chunk_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        return _response(content=chunk_payload)

    async def _synthesis_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        return _response(content=_synthesis_payload(findings=synthesized))

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_two_chunks(),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_chunk_call),
        patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
    ):
        result = run_review(
            _pr_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                synthesis=ReviewSynthesisConfig(enabled=True, max_findings=2),
            ),
        )

    synthesized_titles = [
        finding.title
        for finding in result.findings
        if finding.origin is FindingOrigin.SYNTHESIS
    ]
    assert_that(synthesized_titles).is_equal_to(["Novel cross-file mismatch"])
    assert_that(_outcome(result=result).findings_added).is_equal_to(1)


# --- (q) a malformed findings value is a failure, not an empty answer ---------


@pytest.mark.parametrize(
    "content",
    [
        '{"findings": "none"}',
        '{"findings": {"a": 1}}',
        '{"findings": null}',
        # No ``findings`` key at all: an answer that never mentions findings
        # did not answer, and must not read as "found nothing".
        '{"summary": "all consistent"}',
        "{}",
    ],
)
def test_a_non_list_findings_value_is_a_failed_pass(content: str) -> None:
    """A missing or malformed findings value is a failure, not an empty answer.

    Args:
        content: A JSON object whose ``findings`` value is absent or not a
            list.
    """
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content=content,
    )

    assert_that(_outcome(result=result).failed).is_true()
    assert_that(_outcome(result=result).findings_added).is_equal_to(0)
    reasons = [item.reason for item in result.metadata.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SYNTHESIS_FAILED)
    assert_that(result.metadata.findings_coverage_complete).is_false()


def test_an_empty_findings_list_is_an_empty_success() -> None:
    """The well-formed empty answer stays a success, not a failure."""
    result = _run(
        synthesis=ReviewSynthesisConfig(enabled=True),
        synthesis_content='{"findings": []}',
    )

    assert_that(_outcome(result=result).failed).is_false()
    assert_that(_outcome(result=result).findings_added).is_equal_to(0)
    assert_that(result.metadata.findings_coverage_complete).is_true()
    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("found no cross-file inconsistencies")


# --- (r) an interrupt abandons the extra call rather than holding the run -----


async def test_an_interrupt_abandons_the_extra_call_and_degrades() -> None:
    """A SIGTERM during the extra call is a failed pass, never a hung run.

    The pass runs after every chunk is reviewed, so there is real coverage to
    persist inside the runner's shutdown window; a bare await would hold the
    process in the provider call instead.
    """
    stop = asyncio.Event()
    started = asyncio.Event()

    async def _never_returns(**_kwargs: Any) -> AIResponse:
        # The stop is set from inside the call, so the event can only fire
        # once the provider call is genuinely in flight: a pass that merely
        # checked ``stop.is_set()`` before calling would hang here instead.
        started.set()
        stop.set()
        await asyncio.Event().wait()  # pragma: no cover - cancelled by the race
        raise AssertionError("the abandoned call resumed")

    with patch("lintro.ai.review.synthesis.call_ai", side_effect=_never_returns):
        result = await run_synthesis_pass(
            request=SynthesisPassRequest(
                context=_pr_context(),
                summaries=(),
                existing_findings=(),
                provider=_mock_provider(),
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                config=ReviewSynthesisConfig(enabled=True),
                policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
                budget=CostBudget(max_cost_usd=1.0),
                diff_budget=100_000,
                stop=stop,
            ),
        )

    assert_that(started.is_set()).is_true()
    assert_that(result.findings).is_empty()
    assert_that(result.outcome.failed).is_true()
    reasons = [item.reason for item in result.degradations]
    assert_that(reasons).contains(CoverageDegradationReason.SYNTHESIS_FAILED)


async def test_without_a_stop_event_the_call_is_awaited_normally() -> None:
    """The race is opt-in: no event means the plain await path still runs."""

    async def _answers(**_kwargs: Any) -> AIResponse:
        return _response(content=_synthesis_payload())

    with patch("lintro.ai.review.synthesis.call_ai", side_effect=_answers):
        result = await run_synthesis_pass(
            request=SynthesisPassRequest(
                context=_pr_context(),
                summaries=(),
                existing_findings=(),
                provider=_mock_provider(),
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                config=ReviewSynthesisConfig(enabled=True),
                policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
                budget=CostBudget(max_cost_usd=1.0),
                diff_budget=100_000,
            ),
        )

    assert_that(result.outcome.failed).is_false()
    assert_that(result.findings).is_length(1)


# --- (s) a run that already stopped never spends the extra call ---------------


def test_a_partial_run_never_spends_the_extra_call() -> None:
    """A cost-cap stop leaves the round with no synthesis pass at all."""
    synthesis_calls: list[str] = []
    provider = _mock_provider()

    async def _chunk_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        raise AICostBudgetExceededError("cost cap reached")

    async def _synthesis_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        synthesis_calls.append(user_prompt)
        return _response(content=_synthesis_payload())

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_two_chunks(),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_chunk_call),
        patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
    ):
        result = run_review(
            _pr_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                synthesis=ReviewSynthesisConfig(enabled=True),
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(synthesis_calls).is_empty()
    assert_that(result.metadata.synthesis).is_none()
    assert_that(format_synthesis_note_line(metadata=result.metadata)).is_empty()
