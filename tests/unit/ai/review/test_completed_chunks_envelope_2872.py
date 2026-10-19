# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""The error envelope says how many chunks completed, end to end (#2872).

The chain walk (ADR-0011 D) keys on ``completed_chunks``. It must be ``0``
when the credential dies on the first call, and it must count a chunk that
completed even when that chunk's checkpoint write failed, because the part
on disk is the only other witness and it is best-effort.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError, AIError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.error_contract import build_error_contract
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.session import ReviewSessionOptions

_RESPONSE = (
    '{"summary": "Merge.", "checklist": [{"id": 1, "answer": "yes", '
    '"evidence": "a.py:1"}], "findings": []}'
)


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-opus-5-5"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    provider.complete.return_value = AIResponse(
        content=_RESPONSE,
        model="claude-opus-5-5",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )
    return provider


def _context() -> ReviewContext:
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="b.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="diff --git a/a.py b/a.py\n+x\ndiff --git a/b.py b/b.py\n+y",
        pr_metadata=None,
    )


def _chunks() -> list[ReviewChunk]:
    return [
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


def _run(*, call_ai: object, checkpoint: object | None = None) -> AIError:
    patches = [
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_chunks(),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=call_ai),
    ]
    if checkpoint is not None:
        patches.append(
            patch(
                "lintro.ai.review.incremental_coverage.write_incremental_coverage_part",
                side_effect=checkpoint,
            ),
        )
    for started in patches:
        started.start()
    try:
        with pytest.raises(AIError) as caught:
            run_review(
                _context(),
                options=ReviewSessionOptions(
                    provider=_provider(),
                    ai_config=AIConfig(
                        enabled=True,
                        transport=AITransport.API,
                        max_parallel_calls=1,
                    ),
                    depth=1,
                    checklist_items=[],
                    checklist_text="1. [logic-bug] Example?",
                    classifications=[],
                ),
            )
    finally:
        for started in patches:
            started.stop()
    return caught.value


def test_a_credential_dead_on_the_first_call_reports_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every call fails: the envelope says no chunk completed, so a walk may start."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))

    def _dead(**_kwargs: object) -> None:
        raise AIAuthenticationError("Error code: 401 - authentication_error")

    error = _run(call_ai=_dead)

    envelope = build_error_contract(provider="anthropic", error=error)["error"]
    assert_that(envelope["kind"]).is_equal_to("auth_failed")
    assert_that(envelope["completed_chunks"]).is_equal_to(0)
    assert_that(list(tmp_path.glob("part-*.json"))).is_empty()


def test_a_chunk_whose_checkpoint_failed_still_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunk one completes, its part write fails, the next call dies: count 1.

    No part is on disk, so the count is the only thing stopping a restart
    of the partial round on another lane.
    """
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    checkpoints: list[int] = []

    def _checkpoint(**kwargs: object) -> None:
        collected = kwargs.get("collected")
        checkpoints.append(len(collected) if isinstance(collected, list) else -1)
        msg = "state directory is read-only"
        raise OSError(msg)

    def _call(*, provider: MagicMock, user_prompt: str, **kwargs: object) -> AIResponse:
        # The question pass sees both files; chunk two's prompt is b.py alone.
        # (The two chunk tasks interleave under the semaphore, so "after the
        # checkpoint fired" is not a usable trigger.)
        if (
            "diff --git a/b.py" in user_prompt
            and "diff --git a/a.py" not in user_prompt
        ):
            raise AIAuthenticationError("Error code: 401 - authentication_error")
        response: AIResponse = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        return response

    error = _run(call_ai=_call, checkpoint=_checkpoint)

    envelope = build_error_contract(provider="anthropic", error=error)["error"]
    # Chunk one did complete and its checkpoint was attempted and failed.
    assert_that(checkpoints).is_equal_to([1])
    assert_that(envelope["kind"]).is_equal_to("auth_failed")
    assert_that(envelope["completed_chunks"]).is_equal_to(1)
    assert_that(list(tmp_path.glob("part-*.json"))).is_empty()
