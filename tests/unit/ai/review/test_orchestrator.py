"""Tests for review orchestrator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError, AIProviderError
from lintro.ai.json_response import strip_json_fences
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.chunk_pass import review_chunk
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.errors_taxonomy import ReviewErrorKind, classify_provider_error
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.interrupt import SIGTERM_TIMEOUT_MESSAGE, sigterm_timeout_error
from lintro.ai.review.merge import parse_review_response
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile
from lintro.ai.review.orchestrator import (
    run_review,
    run_review_async,
)
from lintro.ai.review.progress import NullReviewProgress, ReviewProgressCallback
from lintro.ai.review.prompts import (
    PromptInputs,
    build_git_native_review_prompt,
)
from lintro.ai.review.run_planning import resolve_review_chunks
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.session import ChunkRunPlan, ReviewSessionOptions
from lintro.ai.review.state_store import load_ci_state, write_state_part


def _sample_response_json(
    *,
    include_finding: bool = True,
    finding_file: str = "src/main.py",
) -> str:
    finding = (
        {
            "severity": "P1",
            "category": "security",
            "file": finding_file,
            "line": 12,
            "title": "Fail-open default",
            "description": "Unknown status grants access",
            "cause": "else branch returns Active",
            "fix": "Default to Expired",
            "failure_scenario": "An unknown status grants access in production",
            "confidence": "high",
            "checklist_ids": [1],
        }
        if include_finding
        else None
    )
    payload = {
        "summary": "Merge with fixes.",
        "checklist": [
            {"id": 1, "answer": "yes", "evidence": "src/main.py:12"},
        ],
        "findings": [finding] if finding is not None else [],
    }
    return json.dumps(payload)


def _mock_provider(*, content: str) -> MagicMock:
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    # Declare capabilities explicitly: a bare MagicMock attribute is truthy, so
    # without this a single-chunk review would spuriously take the durable-
    # session path. Tests that exercise that path override this.
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    provider.complete.return_value = AIResponse(
        content=content,
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )
    return provider


def test_strip_json_fences_removes_markdown_wrapper() -> None:
    """Fence stripper extracts JSON from markdown code blocks."""
    content = '```json\n{"summary": "ok"}\n```'
    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"summary": "ok"}')


def test_parse_review_response_validates_required_keys() -> None:
    """Parser accepts valid review JSON payloads."""
    payload = parse_review_response(content=_sample_response_json())

    assert_that(payload["summary"]).contains("Merge")
    assert_that(payload["checklist"]).is_length(1)


def _one_file_context() -> ReviewContext:
    """Build a single-file review context."""
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )


def _two_file_context() -> ReviewContext:
    """Build a two-file context matching the mid-run chunk fixtures."""
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="b.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=("diff --git a/a.py b/a.py\n+x\ndiff --git a/b.py b/b.py\n+y"),
        pr_metadata=None,
    )


def test_run_review_marks_cli_transport_tokens_estimated() -> None:
    """CLI transport flags token usage as locally estimated in metadata."""
    provider = _mock_provider(content=_sample_response_json())

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        ),
    ):
        result = run_review(
            _one_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.token_usage_estimated).is_true()
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(
        result.metadata.chunks_total,
    )


def test_run_review_returns_partial_on_cost_cap() -> None:
    """Cost cap mid-run finalizes a partial review instead of erroring."""
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]

    def _recording_call_ai(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):
        response = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_recording_call_ai,
        ),
    ):
        result = run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_cost_usd=0.01,
                    # Keep this mid-run stop deterministic under the patched
                    # recorder; parallel > 1 accepts n−1 overshoot (#1969).
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).is_equal_to("cost cap ($0.01) reached")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that(result.metadata.chunks_total).is_equal_to(2)
    assert_that(result.findings).is_not_empty()


def test_run_review_returns_partial_on_chunk_timeout() -> None:
    """A mid-run CLI timeout persists completed chunks instead of aborting."""
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    seen: list[str] = []

    def _timeout_second_call(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):
        del budget
        seen.append("call")
        if len(seen) >= 2:
            raise AIProviderError("agent CLI timed out after 1800s") from TimeoutError()
        return provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_timeout_second_call,
        ),
    ):
        result = run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.CLI,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("timeout")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that(result.metadata.chunks_total).is_equal_to(2)
    assert_that({record.path for record in result.coverage_records}).contains("a.py")


@pytest.mark.asyncio
async def test_run_review_returns_partial_on_sigterm() -> None:
    """A mid-run SIGTERM persists completed chunks instead of aborting."""
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    seen: list[str] = []
    stop = asyncio.Event()

    async def _hang_second_call(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):
        del budget
        seen.append("call")
        if len(seen) >= 2:
            stop.set()
            await asyncio.sleep(60)
        return provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_hang_second_call,
        ),
    ):
        result = await run_review_async(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.CLI,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                stop=stop,
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("timeout")
    assert_that(result.metadata.stopped_reason).contains("SIGTERM")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that({record.path for record in result.coverage_records}).contains("a.py")


async def test_run_review_persists_when_agent_dies_after_sigterm() -> None:
    """A non-timeout agent death after stop still persists completed chunks."""
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    seen: list[str] = []
    stop = asyncio.Event()

    async def _die_after_stop(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):
        del budget
        seen.append("call")
        if len(seen) >= 2:
            stop.set()
            raise AIProviderError("agent exited 143")
        return provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_die_after_stop,
        ),
    ):
        result = await run_review_async(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.CLI,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                stop=stop,
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("SIGTERM")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that({record.path for record in result.coverage_records}).contains("a.py")


def test_sigterm_timeout_error_classifies_as_timeout() -> None:
    """The SIGTERM envelope must reuse the persistable TIMEOUT kind."""
    error = sigterm_timeout_error()
    assert_that(str(error)).is_equal_to(SIGTERM_TIMEOUT_MESSAGE)
    assert_that(classify_provider_error(provider="", error=error)).is_equal_to(
        ReviewErrorKind.TIMEOUT,
    )


def test_run_review_writes_incremental_coverage_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each finished chunk writes a CI coverage part before the next call."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]

    def _recording_call_ai(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):
        response = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_recording_call_ai,
        ),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_cost_usd=0.01,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    parts = sorted(tmp_path.glob("part-*.json"))
    assert_that(parts).is_not_empty()
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    assert_that({record.path for record in loaded.coverage}).contains("a.py")
    assert_that(loaded.pr_number).is_equal_to(2166)


def test_incremental_state_json_wins_over_downloaded_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current-run state.json must beat a leftover downloaded snapshot."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    monkeypatch.setenv("GITHUB_RUN_ID", "current-run")
    write_state_part(
        state=ReviewState(
            coverage=(CoverageRecord(path="stale.py", patch_hash="deadbeef"),),
            repo="lgtm-hq/py-lintro",
            pr_number=2166,
            head_sha="old-head",
            run_id="old-run",
        ),
        directory=tmp_path,
        sequence=99,
        final=True,
    )
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
                user_prompt,
                system=kwargs.get("system_prompt"),
                max_tokens=kwargs.get("max_tokens", 1024),
            ),
        ),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    assert_that({record.path for record in loaded.coverage}).contains("a.py")
    assert_that({record.path for record in loaded.coverage}).does_not_contain(
        "stale.py",
    )
    assert_that(loaded.run_id).is_equal_to("current-run")
    assert_that(loaded.head_sha).is_not_equal_to("old-head")


def test_incremental_checkpoint_keeps_prior_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-run checkpoint must not wipe carried findings from the artifact."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    prior = ReviewState(
        coverage=(CoverageRecord(path="kept.py", patch_hash="abc123"),),
        findings=(
            FindingRecord(fingerprint="keep-me", title="old nit", file="kept.py"),
        ),
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
                user_prompt,
                system=kwargs.get("system_prompt"),
                max_tokens=kwargs.get("max_tokens", 1024),
            ),
        ),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                prior_state=prior,
            ),
        )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    assert_that({finding.fingerprint for finding in loaded.findings}).contains(
        "keep-me",
    )
    assert_that({finding.file for finding in loaded.findings}).contains("a.py")
    assert_that({finding.title for finding in loaded.findings}).contains(
        "Fail-open default",
    )


def test_incremental_checkpoint_keeps_this_run_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-run checkpoint must persist findings from finished chunks."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]

    def _timeout_b(
        *,
        provider,
        user_prompt,
        **kwargs,
    ):
        # Key on the chunk's own diff line: every chunk prompt now lists all
        # changed files, so a file name alone no longer identifies the chunk.
        if "diff --git a/b.py" in user_prompt or "+y" in user_prompt:
            raise AIProviderError("agent CLI timed out after 1800s") from TimeoutError()
        return provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_timeout_b,
        ),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.CLI,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    assert_that({record.path for record in loaded.coverage}).contains("a.py")
    assert_that({finding.file for finding in loaded.findings}).contains("a.py")
    assert_that({finding.title for finding in loaded.findings}).contains(
        "Fail-open default",
    )
    this_run = next(finding for finding in loaded.findings if finding.file == "a.py")
    assert_that(this_run.description).contains("Unknown status grants access")
    assert_that(this_run.cause).contains("else branch returns Active")
    assert_that(this_run.fix).contains("Default to Expired")


def test_incremental_checkpoint_applies_sensitivity_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focused policy must drop P3 doc-drift findings from the checkpoint."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    payload = {
        "summary": "Merge with fixes.",
        "checklist": [{"id": 1, "answer": "yes", "evidence": "a.py:12"}],
        "findings": [
            {
                "severity": "P1",
                "category": "security",
                "file": "a.py",
                "line": 12,
                "title": "Fail-open default",
                "description": "Unknown status grants access",
                "cause": "else branch returns Active",
                "fix": "Default to Expired",
                "failure_scenario": "An unknown status grants access",
                "confidence": "high",
                "checklist_ids": [1],
            },
            {
                "severity": "P3",
                "category": ReviewCategory.CONTRACT_DRIFT.value,
                "file": "README.md",
                "line": 1,
                "title": "Doc drift note",
                "description": "README is stale",
                "cause": "Docs not updated",
                "fix": "Update README",
                "confidence": "low",
                "checklist_ids": [1],
            },
        ],
    }
    provider = _mock_provider(content=json.dumps(payload))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
                user_prompt,
                system=kwargs.get("system_prompt"),
                max_tokens=kwargs.get("max_tokens", 1024),
            ),
        ),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                sensitivity=resolve_sensitivity_policy(
                    strictness=ReviewStrictness.FOCUSED,
                ),
            ),
        )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    titles = {finding.title for finding in loaded.findings}
    assert_that(titles).contains("Fail-open default")
    assert_that(titles).does_not_contain("Doc drift note")


def test_incremental_checkpoint_keeps_inherited_sibling_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-hash coverage must not resolve findings on unread sibling files."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    monkeypatch.setenv("PR_NUMBER", "2166")
    prior = ReviewState(
        findings=(
            FindingRecord(
                fingerprint="keep-b",
                title="old nit on b",
                file="b.py",
                description="Sibling body",
                cause="sibling cause",
                fix="sibling fix",
            ),
        ),
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="b.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=("diff --git a/a.py b/a.py\n+x\ndiff --git a/b.py b/b.py\n+x"),
        pr_metadata=None,
    )
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
                user_prompt,
                system=kwargs.get("system_prompt"),
                max_tokens=kwargs.get("max_tokens", 1024),
            ),
        ),
    ):
        run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                prior_state=prior,
            ),
        )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=2166,
    )
    sibling = next(finding for finding in loaded.findings if finding.file == "b.py")
    assert_that(sibling.status).is_equal_to(FindingStatus.OPEN)
    assert_that(sibling.title).is_equal_to("old nit on b")


def test_parallel_timeout_keeps_completed_sibling() -> None:
    """A timeout on one worker must still persist a sibling that already finished."""
    provider = _mock_provider(content=_sample_response_json(finding_file="a.py"))
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]

    def _timeout_b(
        *,
        provider,
        user_prompt,
        **kwargs,
    ):
        # Key on the chunk's own diff line: every chunk prompt now lists all
        # changed files, so a file name alone no longer identifies the chunk.
        if "diff --git a/b.py" in user_prompt or "+y" in user_prompt:
            raise AIProviderError("agent CLI timed out after 1800s") from TimeoutError()
        return provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_timeout_b,
        ),
    ):
        result = run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.CLI,
                    max_parallel_calls=2,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that({record.path for record in result.coverage_records}).contains("a.py")


def test_run_review_partial_when_cost_cap_before_any_chunk() -> None:
    """Cap tripping before any chunk completes returns an actionable partial.

    A depth-2 chunk overspends the cap on its question-generation call, so the
    main review budget check raises before the chunk produces a partial. The
    result must be a clean, empty partial (``partial=True``, zero chunks
    reviewed) rather than the generic abort error.
    """
    provider = _mock_provider(content=_sample_response_json())

    def _recording_call_ai(
        *,
        provider,
        budget=None,
        **kwargs,
    ):
        response = provider.complete(
            kwargs.get("user_prompt", ""),
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=_recording_call_ai,
    ):
        result = run_review(
            _one_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_cost_usd=0.005,
                ),
                depth=2,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(0)
    assert_that(result.metadata.stopped_reason).contains("cost cap")
    assert_that(result.findings).is_empty()


def test_run_review_raises_on_genuine_provider_error_mid_review() -> None:
    """A real provider error mid-review still raises, never a silent partial."""
    provider = _mock_provider(content=_sample_response_json())
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    seen: list[str] = []

    def _flaky_call_ai(
        *,
        provider,
        budget=None,
        **kwargs,
    ):
        del budget
        seen.append("call")
        if len(seen) >= 2:
            raise AIError("anthropic: overloaded_error")
        return provider.complete(
            kwargs.get("user_prompt", ""),
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_flaky_call_ai,
        ),
        pytest.raises(AIError),
    ):
        run_review(
            _two_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )


def test_run_review_depth1_returns_review_result() -> None:
    """Depth 1 review produces findings from mocked provider response."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    checklist_items = [
        ChecklistItem(
            id=1,
            question="Example?",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    provider = _mock_provider(content=_sample_response_json())

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=checklist_items,
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.summary).contains("Merge")
    assert_that(result.findings).is_not_empty()
    assert_that(result.has_p1_findings).is_true()


def test_run_review_empty_diff_returns_empty_result() -> None:
    """Empty diff returns graceful empty review result."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[],
        unified_diff="",
        pr_metadata=None,
    )
    provider = _mock_provider(content="{}")

    result = run_review(
        context,
        options=ReviewSessionOptions(
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
        ),
    )

    assert_that(result.summary).contains("No changes")
    assert_that(result.findings).is_empty()


def test_run_review_depth2_calls_provider_twice() -> None:
    """Depth 2 runs question generation before the main review pass."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    provider = _mock_provider(
        content='{"generated_questions": [{"id": "G1", "question": "Extra?"}]}',
    )
    provider.complete.side_effect = [
        AIResponse(
            content='{"generated_questions": [{"id": "G1", "question": "Extra?"}]}',
            model="claude-sonnet-4-20250514",
            input_tokens=50,
            output_tokens=20,
            cost_estimate=0.005,
            provider="anthropic",
        ),
        AIResponse(
            content=_sample_response_json(include_finding=False),
            model="claude-sonnet-4-20250514",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
            provider="anthropic",
        ),
    ]

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=2,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(150)
    assert_that(result.metadata.token_usage["completion"]).is_equal_to(70)
    assert_that(result.metadata.cost_estimate_usd).is_equal_to(0.015)


def _single_chunk() -> ReviewChunk:
    """Build a one-file review chunk for direct ``_review_chunk`` tests."""
    return ReviewChunk(
        id=1,
        files=["src/main.py"],
        diff="diff --git a/src/main.py b/src/main.py\n+change",
        relationship=REL_SINGLE_FILE,
    )


def _single_file_context() -> ReviewContext:
    """Build a minimal review context for direct ``_review_chunk`` tests."""
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )


def _chunk_run_plan(*, budget: CostBudget) -> ChunkRunPlan:
    """Build a depth-3 chunk plan for the single-file fixture context.

    Args:
        budget: Cost budget the chunk's provider calls record against.

    Returns:
        A plan whose only variation from the defaults is the budget.
    """
    return ChunkRunPlan(
        context=_single_file_context(),
        provider=MagicMock(),
        ai_config=AIConfig(enabled=True, transport=AITransport.API),
        depth=3,
        checklist_items=[],
        checklist_text="1. [logic-bug] Example?",
        classifications=[],
        lint_results=None,
        budget=budget,
        progress=NullReviewProgress(),
        repo_root="",
        use_one_shot=False,
        strictness_section="",
        next_generated_checklist_id=100,
        diff_budget=0,
    )


async def test_review_chunk_checks_budget_before_each_provider_call() -> None:
    """Depth-3 review checks the budget before every intra-chunk call."""
    events: list[str] = []
    budget = CostBudget(max_cost_usd=None)
    original_check = budget.check

    def _record_check() -> None:
        events.append("check")
        original_check()

    def _fake_call_ai(*, budget: CostBudget, **kwargs: object) -> AIResponse:
        del budget, kwargs
        events.append("call")
        return AIResponse(
            content=_sample_response_json(include_finding=False),
            model="auto",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
            provider="cursor",
        )

    with (
        patch.object(budget, "check", side_effect=_record_check),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_fake_call_ai),
    ):
        await review_chunk(
            chunk=_single_chunk(),
            plan=_chunk_run_plan(budget=budget),
        )

    # Three provider calls (extra checklist, main review, adversarial), each
    # preceded by a budget check.
    assert_that(events.count("call")).is_equal_to(3)
    for index, event in enumerate(events):
        if event == "call":
            assert_that(events[index - 1]).is_equal_to("check")


async def test_review_chunk_budget_stops_runaway_calls() -> None:
    """An exhausted budget halts the chunk before overspending on more calls."""
    calls: list[str] = []
    budget = CostBudget(max_cost_usd=0.01)

    def _fake_call_ai(*, budget: CostBudget, **kwargs: object) -> AIResponse:
        del kwargs
        calls.append("call")
        response = AIResponse(
            content=_sample_response_json(include_finding=False),
            model="auto",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.02,
            provider="cursor",
        )
        budget.record(response.cost_estimate)
        return response

    with patch("lintro.ai.review.provider_call.call_ai", side_effect=_fake_call_ai):
        with pytest.raises(AIError):
            await review_chunk(
                chunk=_single_chunk(),
                plan=_chunk_run_plan(budget=budget),
            )

    # The first depth-2 call overspends the $0.01 cap; the budget check gates
    # the next call before a runaway depth-3 sweep can fire.
    assert_that(len(calls)).is_less_than(3)


def test_resolve_review_chunks_uses_fast_path_for_small_diff(
    sample_review_context: ReviewContext,
) -> None:
    """Small diffs within budget collapse to a single chunk."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=10_000,
        classifications=[],
    )

    assert_that(chunks).is_length(1)
    assert_that(chunks[0].files).is_length(5)
    assert_that(chunks[0].relationship).is_equal_to("directory-prefix")


def test_resolve_review_chunks_semantic_when_over_budget(
    sample_review_context: ReviewContext,
) -> None:
    """Oversized diffs still use semantic chunking."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=50,
        classifications=[],
    )

    assert_that(chunks).is_not_empty()
    assert_that(len(chunks)).is_greater_than(1)


def test_resolve_review_chunks_skips_fast_path_when_forced(
    sample_review_context: ReviewContext,
) -> None:
    """Thorough strictness can force semantic chunking even for small diffs."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=10_000,
        classifications=[],
        force_semantic_chunking=True,
    )

    assert_that(len(chunks)).is_greater_than(1)


def test_run_review_parallelizes_multiple_chunks(tmp_path: Path) -> None:
    """Multiple chunks run concurrently up to max_parallel_calls."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path=f"src/file{index}.py",
                status="modified",
                additions=1,
                deletions=0,
            )
            for index in range(4)
        ],
        unified_diff="diff",
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    chunks = [
        ReviewChunk(
            id=index + 1,
            files=[f"src/file{index}.py"],
            diff=f"+line{index}",
            relationship="single-file",
        )
        for index in range(4)
    ]
    provider = _mock_provider(content=_sample_response_json(include_finding=False))
    active = 0
    max_active = 0

    async def _track_concurrency(
        *,
        provider: MagicMock,
        user_prompt: str,
        **kwargs: object,
    ) -> AIResponse:
        """Record how many chunk reviews overlap on the event loop.

        Args:
            provider: The provider under review.
            user_prompt: Ignored prompt text.
            **kwargs: Ignored call arguments.

        Returns:
            The provider's canned response.
        """
        del user_prompt, kwargs
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        response: AIResponse = provider.complete("prompt")
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_track_concurrency),
    ):
        run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=4,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(max_active).is_greater_than(1)
    assert_that(max_active).is_less_than_or_equal_to(4)
    assert_that(provider.complete.call_count).is_equal_to(4)


def _multi_chunk_context(*, tmp_path: Path, count: int = 4) -> ReviewContext:
    """Build a multi-file review context for fan-out tests.

    Args:
        tmp_path: Temporary repository root.
        count: Number of changed files / chunks to synthesize.

    Returns:
        A ``ReviewContext`` with ``count`` modified files.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path=f"src/file{index}.py",
                status="modified",
                additions=1,
                deletions=0,
            )
            for index in range(count)
        ],
        unified_diff="diff",
        pr_metadata=None,
        repo_root=str(tmp_path),
    )


def _multi_chunks(*, count: int = 4) -> list[ReviewChunk]:
    """Build ``count`` single-file review chunks.

    Args:
        count: Number of chunks.

    Returns:
        Ordered list of single-file chunks.
    """
    return [
        ReviewChunk(
            id=index + 1,
            files=[f"src/file{index}.py"],
            diff=f"+line{index}",
            relationship="single-file",
        )
        for index in range(count)
    ]


def test_run_review_serializes_when_cost_cap_is_set(tmp_path: Path) -> None:
    """A cost cap forces serial chunk reviews so queue order cannot invert."""
    context = _multi_chunk_context(tmp_path=tmp_path, count=4)
    chunks = _multi_chunks(count=4)
    provider = _mock_provider(content=_sample_response_json(include_finding=False))
    active = 0
    max_active = 0

    async def _track_concurrency(
        *,
        provider: MagicMock,
        budget: CostBudget | None = None,
        **kwargs: object,
    ) -> AIResponse:
        """Overlap chunk reviews and charge a tiny cost against the budget.

        Args:
            provider: Mock provider returning the canned response.
            budget: Session cost budget; recorded when present.
            **kwargs: Ignored call arguments.

        Returns:
            The provider's canned response.
        """
        del kwargs
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        response: AIResponse = provider.complete("prompt")
        if budget is not None:
            budget.record(0.001)
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_track_concurrency,
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=4,
                    max_cost_usd=1.0,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(max_active).is_equal_to(1)
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(4)


def test_run_review_parallelizes_depth_two_chunks(tmp_path: Path) -> None:
    """Depth ≥ 2 chunk refinement fans out under max_parallel_calls."""
    context = _multi_chunk_context(tmp_path=tmp_path, count=4)
    chunks = _multi_chunks(count=4)
    provider = _mock_provider(content=_sample_response_json(include_finding=False))
    active = 0
    max_active = 0

    async def _track_concurrency(
        *,
        provider: MagicMock,
        **kwargs: object,
    ) -> AIResponse:
        """Track overlapping depth-2 provider calls across chunks.

        Args:
            provider: Mock provider returning the canned response.
            **kwargs: Ignored call arguments.

        Returns:
            The provider's canned response.
        """
        del kwargs
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        response: AIResponse = provider.complete("prompt")
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_track_concurrency),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=4,
                ),
                depth=2,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(max_active).is_greater_than(1)
    assert_that(max_active).is_less_than_or_equal_to(4)
    assert_that(result.metadata.chunks_reviewed).is_equal_to(4)


def test_run_review_merges_chunks_in_index_order(tmp_path: Path) -> None:
    """Findings merge in chunk-index order even when slow chunks finish last."""
    context = _multi_chunk_context(tmp_path=tmp_path, count=3)
    chunks = _multi_chunks(count=3)

    async def _slow_first_chunk(
        *,
        provider: MagicMock,
        user_prompt: str,
        **kwargs: object,
    ) -> AIResponse:
        """Delay the first chunk so later chunks complete first.

        Args:
            provider: Unused mock provider.
            user_prompt: Prompt text used to identify the chunk.
            **kwargs: Ignored call arguments.

        Returns:
            A review payload whose finding file matches the chunk.
        """
        del provider, kwargs
        if "+line0" in user_prompt:
            await asyncio.sleep(0.08)
            path = "src/file0.py"
        elif "+line1" in user_prompt:
            path = "src/file1.py"
        else:
            path = "src/file2.py"
        payload = {
            "summary": f"Summary for {path}",
            "checklist": [],
            "findings": [
                {
                    "severity": "P2",
                    "category": "logic",
                    "file": path,
                    "line": 1,
                    "title": f"Issue in {path}",
                    "description": "x",
                    "cause": "y",
                    "fix": "z",
                    "failure_scenario": "w",
                    "confidence": "high",
                    "checklist_ids": [],
                },
            ],
        }
        return AIResponse(
            content=json.dumps(payload),
            model="auto",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.0,
            provider="anthropic",
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_slow_first_chunk,
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=_mock_provider(content=_sample_response_json()),
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=3,
                    max_cost_usd=1.0,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    finding_files = [finding.file for finding in result.findings]
    assert_that(finding_files).is_equal_to(
        ["src/file0.py", "src/file1.py", "src/file2.py"],
    )


def test_run_review_records_phase_timings(tmp_path: Path) -> None:
    """Review metadata includes context_collection/provider/parse_merge timings."""
    context = _multi_chunk_context(tmp_path=tmp_path, count=2)
    chunks = _multi_chunks(count=2)
    provider = _mock_provider(content=_sample_response_json(include_finding=False))

    async def _fast_call(
        *,
        provider: MagicMock,
        **kwargs: object,
    ) -> AIResponse:
        """Return the canned provider response without sleeping.

        Args:
            provider: Mock provider under test.
            **kwargs: Ignored call arguments.

        Returns:
            The provider's canned response.
        """
        del kwargs
        response: AIResponse = provider.complete("prompt")
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_fast_call,
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                context_collection_seconds=0.123,
            ),
        )

    timings = result.metadata.phase_timings
    assert_that(timings).contains_key("context_collection")
    assert_that(timings).contains_key("provider")
    assert_that(timings).contains_key("parse_merge")
    assert_that(timings["context_collection"]).is_close_to(0.123, 0.001)
    assert_that(timings["provider"]).is_greater_than_or_equal_to(0.0)
    assert_that(timings["parse_merge"]).is_greater_than_or_equal_to(0.0)
    assert_that(result.metadata.duration_seconds).is_greater_than_or_equal_to(0.0)


def test_run_review_budget_cutoff_keeps_completed_under_parallelism(
    tmp_path: Path,
) -> None:
    """Mid-flight cost-cap stops scheduling; completed chunks survive merge."""
    context = _multi_chunk_context(tmp_path=tmp_path, count=4)
    chunks = _multi_chunks(count=4)
    call_count = 0

    async def _expensive_call(
        *,
        provider: MagicMock,
        budget: CostBudget | None = None,
        **kwargs: object,
    ) -> AIResponse:
        """Charge enough that later chunks trip the cost cap.

        Args:
            provider: Unused mock provider.
            budget: Session cost budget charged per call.
            **kwargs: Ignored call arguments.

        Returns:
            A finding-free review payload.
        """
        del provider, kwargs
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        if budget is not None:
            budget.record(0.4)
        return AIResponse(
            content=_sample_response_json(include_finding=False),
            model="auto",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.4,
            provider="anthropic",
        )

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_expensive_call,
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=_mock_provider(content=_sample_response_json()),
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=2,
                    max_cost_usd=0.5,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.chunks_reviewed).is_greater_than(0)
    assert_that(result.metadata.chunks_reviewed).is_less_than(4)
    assert_that(result.metadata.stopped_reason).contains("cost cap")
    assert_that(call_count).is_less_than(4)


def test_run_review_aborts_progress_when_chunk_review_fails() -> None:
    """Progress tracker receives on_abort when a chunk review raises."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    provider = _mock_provider(content=_sample_response_json())
    progress = _RecordingProgress()

    with (
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=RuntimeError("provider failed"),
        ),
        pytest.raises(ReviewExecutionError),
    ):
        run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                progress=progress,
            ),
        )

    # The run starts, reports the failure, aborts, and never completes.
    assert_that(progress.events.count("on_start")).is_equal_to(1)
    assert_that(progress.events.count("on_error")).is_equal_to(1)
    assert_that(progress.events.count("on_abort")).is_equal_to(1)
    assert_that(progress.events).does_not_contain("on_complete")


def test_run_review_propagates_chunk_error_when_progress_abort_raises() -> None:
    """Progress cleanup errors must not mask the original chunk review failure."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    provider = _mock_provider(content=_sample_response_json())
    progress = MagicMock(spec=ReviewProgressCallback)
    progress.on_abort.side_effect = BrokenPipeError()

    with (
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=RuntimeError("provider failed"),
        ),
        pytest.raises(ReviewExecutionError) as exc_info,
    ):
        run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                progress=progress,
            ),
        )

    assert_that(exc_info.value.cause_message).contains("provider failed")
    progress.on_start.assert_called_once()
    progress.on_abort.assert_called_once()
    progress.on_complete.assert_not_called()


def test_run_review_returns_result_when_progress_complete_raises() -> None:
    """Progress cleanup errors must not discard a successful review result."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    checklist_items = [
        ChecklistItem(
            id=1,
            question="Example?",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    provider = _mock_provider(content=_sample_response_json())
    progress = MagicMock(spec=ReviewProgressCallback)
    progress.on_complete.side_effect = BrokenPipeError()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
            timeout=kwargs.get("timeout", 60.0),
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=1,
                checklist_items=checklist_items,
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                progress=progress,
            ),
        )

    assert_that(result.summary).contains("Merge")
    progress.on_complete.assert_called_once_with(total_findings=1)


def test_run_review_uses_git_native_prompt_for_cli_transport() -> None:
    """CLI transport uses git-native prompts for non-Cursor providers."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    provider = _mock_provider(content=_sample_response_json())
    provider.name = "anthropic"

    built: list[dict[str, Any]] = []

    def _build(**kwargs: Any) -> tuple[str, str]:
        """Record one git-native prompt build.

        Args:
            **kwargs: Prompt-builder keyword arguments.

        Returns:
            tuple[str, str]: A stand-in system and user prompt pair.
        """
        built.append(kwargs)
        return ("system", "user")

    with patch(
        "lintro.ai.review.response_pipeline.build_git_native_review_prompt",
        _build,
    ):
        with patch(
            "lintro.ai.review.provider_call.call_ai",
            return_value=provider.complete("prompt"),
        ):
            result = run_review(
                context,
                options=ReviewSessionOptions(
                    provider=provider,
                    ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
                    depth=1,
                    checklist_items=[],
                    checklist_text="1. [logic-bug] Example?",
                    classifications=[],
                ),
            )

    # One chunk, so exactly one git-native prompt, and the review still lands.
    assert_that(built).is_length(1)
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)


def test_build_git_native_review_prompt_embeds_diff_when_requested(
    sample_review_context: ReviewContext,
) -> None:
    """Git-native prompts can inline the diff for budget-fitting chunks."""
    chunk = ReviewChunk(
        id=1,
        files=["src/lib/math.py"],
        diff="diff --git a/src/lib/math.py b/src/lib/math.py\n+1\n",
        relationship="single-file",
        metadata_note=None,
    )

    _, user_prompt = build_git_native_review_prompt(
        inputs=PromptInputs(
            chunk=chunk,
            context=sample_review_context,
            checklist_text="1. [logic-bug] Example?",
            checklist_count=1,
            interaction_paths="(none)",
        ),
        embed_diff=True,
    )

    assert_that(user_prompt).contains("<pull_request_diff>")
    assert_that(user_prompt).contains("src/lib/math.py")
    assert_that(user_prompt).does_not_contain("git diff")


def test_build_git_native_review_prompt_uses_git_command_when_not_embedded(
    sample_review_context: ReviewContext,
) -> None:
    """Large diffs keep agentic git diff instructions under the opt-out.

    The delegated ``git diff`` command bypasses secret redaction, so it is
    only emitted when the caller explicitly opts out of the redaction
    guarantee via ``allow_unredacted_git_native``.
    """
    chunk = ReviewChunk(
        id=1,
        files=["src/lib/math.py"],
        diff="diff --git a/src/lib/math.py b/src/lib/math.py\n+1\n",
        relationship="single-file",
        metadata_note=None,
    )

    _, user_prompt = build_git_native_review_prompt(
        inputs=PromptInputs(
            chunk=chunk,
            context=sample_review_context,
            checklist_text="1. [logic-bug] Example?",
            checklist_count=1,
            interaction_paths="(none)",
        ),
        embed_diff=False,
        allow_unredacted_git_native=True,
    )

    assert_that(user_prompt).contains("git diff")
    assert_that(user_prompt).does_not_contain("<pull_request_diff>")


class _RecordingProgress:
    """Progress callback that records the lifecycle events it receives.

    Used instead of a mock so tests assert on the observable event sequence
    rather than on how the collaborator was called (#2315).

    Attributes:
        events: Callback names in the order the orchestrator invoked them.
    """

    events: list[str]

    def __init__(self) -> None:
        """Start with an empty event log."""
        self.events = []

    def __getattr__(self, name: str) -> Any:
        """Record any ``on_*`` callback the orchestrator invokes.

        Args:
            name: Callback name being looked up.

        Returns:
            Any: A recorder for ``on_*`` names.

        Raises:
            AttributeError: For any non-callback attribute.
        """
        if not name.startswith("on_"):
            raise AttributeError(name)

        def _record(*_args: Any, **_kwargs: Any) -> None:
            self.events.append(name)

        return _record


def _capability_provider(
    *,
    supports_sessions: bool,
    session_events: list[str] | None = None,
) -> MagicMock:
    """Build a mock provider declaring a session capability.

    Args:
        supports_sessions: Value of ``capabilities.supports_sessions``.
        session_events: Optional list that records ``begin``/``end`` in the
            order the orchestrator drives the durable session, so tests assert
            on the session lifecycle rather than on mock call bookkeeping
            (#2315).

    Returns:
        A configured provider mock.
    """
    provider = _mock_provider(content=_sample_response_json())
    provider.capabilities = ProviderCapabilities(
        supports_sessions=supports_sessions,
    )
    if session_events is not None:
        provider.begin_durable_session.side_effect = lambda *_args, **_kwargs: (
            session_events.append("begin")
        )
        provider.end_durable_session.side_effect = lambda *_args, **_kwargs: (
            session_events.append("end")
        )
    return provider


def _run_single_chunk_review(provider: MagicMock) -> None:
    """Run a one-chunk review against *provider*.

    Args:
        provider: The provider mock under test.
    """
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
            )
        ),
    ):
        run_review(
            _one_file_context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )


def test_run_review_opens_durable_session_when_capability_declared() -> None:
    """A provider declaring session support gets its session opened and closed."""
    session_events: list[str] = []
    provider = _capability_provider(
        supports_sessions=True,
        session_events=session_events,
    )

    _run_single_chunk_review(provider)

    assert_that(session_events).is_equal_to(["begin", "end"])


def test_run_review_skips_durable_session_without_capability() -> None:
    """A provider declaring no session support is never asked to open one."""
    session_events: list[str] = []
    provider = _capability_provider(
        supports_sessions=False,
        session_events=session_events,
    )

    _run_single_chunk_review(provider)

    assert_that(session_events).is_empty()


def test_run_review_metadata_records_reviewed_and_skipped_files() -> None:
    """Run metadata carries the reviewed paths and every skip with its reason."""
    provider = _mock_provider(content=_sample_response_json())
    context = _one_file_context()
    context.skipped_files = [
        SkippedFile(path="docs/README.md", reason=FileSkipReason.PATH_FILTER),
    ]

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            ),
        )

    assert_that(result.metadata.reviewed_paths).is_equal_to(("src/main.py",))
    assert_that(result.metadata.files_reviewed).is_equal_to(1)
    assert_that(result.metadata.files_total).is_equal_to(2)
    assert_that(result.metadata.skipped_files[0].reason).is_equal_to(
        FileSkipReason.PATH_FILTER,
    )


def test_run_review_records_files_no_custom_agent_covered() -> None:
    """An agents-only run reports files outside every agent's scope as skipped."""
    provider = _mock_provider(content=_sample_response_json())
    context = _one_file_context()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: (
            provider.complete(
                user_prompt,
                system=system_prompt,
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        ),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(enabled=True),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                run_builtin_checklist=False,
            ),
        )

    assert_that(result.metadata.reviewed_paths).is_empty()
    assert_that(result.metadata.skipped_files[0].reason).is_equal_to(
        FileSkipReason.AGENT_SCOPE,
    )


async def test_generated_checklist_ids_capped_at_stride() -> None:
    """Model-controlled question counts cannot cross the per-chunk id stride.

    Parallel chunks get disjoint id ranges of ``GENERATED_CHECKLIST_ID_STRIDE``;
    accepting more generated questions than the stride would collide with the
    next chunk's range and corrupt the checklist merge (#1969).
    """
    from lintro.ai.budget import CostBudget
    from lintro.ai.review.checklist_pass import (
        GENERATED_CHECKLIST_ID_STRIDE,
        generate_extra_checklist,
    )

    oversized = [
        {"id": f"G{i}", "question": f"Question {i}?"}
        for i in range(GENERATED_CHECKLIST_ID_STRIDE + 10)
    ]
    payload = json.dumps({"generated_questions": oversized})
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="diff --git a/a.py b/a.py\n+x",
        pr_metadata=None,
    )
    chunk = ReviewChunk(
        id=1,
        files=["a.py"],
        diff="+x",
        relationship=REL_SINGLE_FILE,
    )
    response = AIResponse(
        content=payload,
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
        provider="anthropic",
    )

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        return_value=response,
    ):
        text, next_id, _usage = await generate_extra_checklist(
            chunk=chunk,
            context=context,
            provider=_mock_provider(content=payload),
            ai_config=AIConfig(),
            budget=CostBudget(max_cost_usd=None),
            next_generated_checklist_id=100,
        )

    assert_that(next_id).is_equal_to(100 + GENERATED_CHECKLIST_ID_STRIDE)
    assert_that(text.splitlines()).is_length(GENERATED_CHECKLIST_ID_STRIDE)
