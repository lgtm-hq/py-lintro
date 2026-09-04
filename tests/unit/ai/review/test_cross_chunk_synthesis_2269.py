"""Tests for the cross-chunk synthesis pass (issue #2269).

The pass is one extra provider call made after the chunk findings are merged,
asked only for inconsistencies between files reviewed in different chunks. It
ships off by default, so most of what these tests assert is that a default run
is byte-identical to one from before the pass existed, and that an enabled run
degrades rather than fails when the pass cannot do its job.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from pydantic import ValidationError
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_render import format_synthesis_note_line
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.github_sticky import build_sticky_comment
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.orchestrator import guard_changed_paths, run_review
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.synthesis_prompt import (
    build_synthesis_prompt,
    cross_chunk_paths,
    guarded_changed_paths,
    select_synthesis_diff,
)
from lintro.ai.review.timings import ReviewPhase
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
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


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
        input_tokens=100,
        output_tokens=50,
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

    Returns:
        The review result.
    """
    provider = _mock_provider()
    chunk_sink = chunk_calls if chunk_calls is not None else []
    synthesis_sink = synthesis_calls if synthesis_calls is not None else []

    async def _chunk_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        chunk_sink.append(user_prompt)
        return _response(content=_chunk_payload())

    async def _synthesis_call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        synthesis_sink.append(user_prompt)
        if synthesis_error is not None:
            raise synthesis_error
        return _response(content=synthesis_content or _synthesis_payload())

    with (
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=chunks if chunks is not None else _two_chunks(),
        ),
        patch("lintro.ai.review.orchestrator.call_ai", side_effect=_chunk_call),
        patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
    ):
        return run_review(
            context if context is not None else _pr_context(),
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
            synthesis=synthesis,
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
    sticky = build_sticky_comment(result=result, head_sha="deadbeef")
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

    payload = review_result_to_dict(result=result)
    assert_that(payload["synthesis"]).is_equal_to(
        {"enabled": True, "findings_added": 1, "truncated": False},
    )
    assert_that(payload["findings"][0]["origin"]).is_equal_to("synthesis")

    note = format_synthesis_note_line(metadata=result.metadata)
    assert_that(note).contains("added 1 cross-file finding")
    console = Console(record=True)
    render_review_terminal(result=result, console=console)
    assert_that(console.export_text()).contains("Cross-chunk synthesis")


def test_synthesis_records_its_own_phase_span() -> None:
    """Cost and wall-clock land on the existing #2148 timing surfaces."""
    result = _run(synthesis=ReviewSynthesisConfig(enabled=True))

    timings = result.metadata.timings
    assert_that(timings).is_not_none()
    names = [span.name for span in timings.phases]
    assert_that(names).contains(ReviewPhase.SYNTHESIS.value)
    assert_that(result.metadata.token_usage["total"]).is_equal_to(450)


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

    assert_that(guarded_changed_paths(context=context)).is_equal_to(
        guard_changed_paths(context=context),
    )
    assert_that(guarded_changed_paths(context=context)).is_equal_to(
        (
            "pkg/renamed.py",
            "pkg/old_name.py",
            "pkg/copy.py",
            "pkg/source.py",
            "pkg/plain.py",
        ),
    )


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
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=_two_chunks(),
        ),
        patch("lintro.ai.review.orchestrator.call_ai", side_effect=_chunk_call),
        patch("lintro.ai.review.synthesis.call_ai", side_effect=_synthesis_call),
    ):
        result = run_review(
            _pr_context(),
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
        diff_budget=len(_CALLER_DIFF) // 4 + 1,
    )

    assert_that(truncated).is_true()
    assert_that(diff).contains("pkg/caller.py")


def test_truncated_input_is_recorded_and_declared_in_the_prompt() -> None:
    """A cut input marks coverage degraded and warns the model about it."""
    context = _pr_context()
    diff, truncated = select_synthesis_diff(
        context=context,
        summaries=(),
        diff_budget=len(_SIGNATURE_DIFF) // 4 + 1,
    )
    _system, user_prompt = build_synthesis_prompt(
        context=context,
        summaries=(),
        diff=diff,
        truncated=truncated,
        max_findings=5,
    )

    assert_that(truncated).is_true()
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


def test_new_models_and_enums_are_exported() -> None:
    """The new value objects reach callers through the package namespaces."""
    from lintro.ai.review import enums, models

    assert_that(enums.FindingOrigin).is_same_as(FindingOrigin)
    assert_that(models.ChunkSummary).is_same_as(ChunkSummary)
    assert_that(enums.__all__).contains("FindingOrigin")
    assert_that(models.__all__).contains("ChunkSummary", "SynthesisOutcome")
