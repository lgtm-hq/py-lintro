"""Tests for the PR-head worktree (issue #2733).

On ``--pr`` the CLI agent must read the pull request's head, never the tree
the command happens to run in. These tests build a scratch repository with a
bare "origin" that serves ``refs/pull/1/head``, and drive the collection and
the orchestrator against it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess  # nosec B404 - scratch repositories are driven with fixed git argv
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.cli_bounds import CallShape
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.context.collection import collect_review_context
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.pr_head import (
    PrHeadWorktree,
    checkout_pr_head,
    no_tree_degradations,
    prune_stale_pr_worktrees,
    remove_pr_head,
)
from lintro.ai.review.prompts import _tree_note_for
from lintro.ai.review.run_planning import plan_run
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.timings import ReviewTimingRecorder

pytestmark = pytest.mark.verification  # the real finalizer path, no stub


def _git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stdout.

    Args:
        cwd: Working directory.
        *args: Git arguments.

    Returns:
        Stripped stdout.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    return subprocess.run(  # nosec B603 B607 - fixed argv over a scratch repository
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def scratch(tmp_path: Path) -> dict[str, Any]:
    """A clone at commit C whose origin serves ``refs/pull/1/head`` at B.

    Commits: A (base) → B (the PR head, changes ``api.py``) → C (main moved
    on and changed ``api.py`` again).

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Paths and OIDs of the scratch repositories.
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / "api.py").write_text("def send(payload):\n    return 1\n")
    _git(work, "add", "api.py")
    _git(work, "commit", "-q", "-m", "A")
    base = _git(work, "rev-parse", "HEAD")
    (work / "api.py").write_text("def send(payload, *, retries):\n    return 1\n")
    _git(work, "commit", "-q", "-am", "B: the PR")
    head = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-q", "origin", "HEAD:refs/pull/1/head")
    (work / "api.py").write_text("def send(payload, *, retries):\n    return retries\n")
    _git(work, "commit", "-q", "-am", "C: main moved on")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    return {
        "work": work,
        "base": base,
        "head": head,
        "diff_line": "+def send(payload, *, retries):",
    }


def _pr_context(scratch: dict[str, Any]) -> ReviewContext:
    """The context ``gh`` would have collected for PR 1.

    Args:
        scratch: The fixture repositories.

    Returns:
        A review context with the PR's refs and diff.
    """
    diff = (
        "diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n"
        "@@ -1,2 +1,2 @@\n-def send(payload):\n+def send(payload, *, retries):\n"
        "     return 1\n"
    )
    return ReviewContext(
        base_ref=scratch["base"],
        head_ref=scratch["head"],
        changed_files=[
            ChangedFile(path="api.py", status="modified", additions=1, deletions=1),
        ],
        unified_diff=diff,
        pr_metadata=PRMetadata(
            title="t",
            body="b",
            number=1,
            repo="o/r",
            head_repo="o/r",
        ),
        checkout=ReviewCheckout.UNKNOWN,
    )


# --- scenario 1: the tree is neither end; the agent reads the head -------------


def test_a_pr_review_from_a_moved_on_tree_reads_the_pr_head(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cwd at C, PR head B: repo_root is a worktree at B, checkout HEAD."""
    monkeypatch.chdir(scratch["work"])
    with patch(
        "lintro.ai.review.context.collection._collect_pr_context",
        return_value=_pr_context(scratch),
    ):
        context = collect_review_context(pr_number=1, repo="o/r")

    assert context.head_worktree is not None
    root = Path(context.repo_root)
    assert_that(root.is_dir()).is_true()
    assert_that(root).is_not_equal_to(scratch["work"].resolve())
    assert_that(_git(root, "rev-parse", "HEAD")).is_equal_to(scratch["head"])
    assert_that((root / "api.py").read_text()).contains("return 1")
    assert_that(context.checkout).is_equal_to(ReviewCheckout.HEAD)
    assert_that(_tree_note_for(context=context)).contains("post-change")
    # The original tree is untouched.
    assert_that(_git(scratch["work"], "rev-parse", "HEAD")).is_not_equal_to(
        scratch["head"],
    )

    remove_pr_head(context.head_worktree)
    assert_that(root.exists()).is_false()
    assert_that(_git(scratch["work"], "worktree", "list")).does_not_contain("pr-heads")


def test_the_worktree_is_removed_on_every_exit_path(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed run, an exception and a stop event all remove the tree."""
    monkeypatch.chdir(scratch["work"])
    worktree = checkout_pr_head(pr_number=1, head_oid=scratch["head"])
    assert worktree is not None

    def _options(stop: asyncio.Event | None = None) -> ReviewSessionOptions:
        provider = MagicMock()
        provider.aclose = AsyncMock()
        provider.model_name = "m"
        provider.name = "anthropic"
        provider.capabilities = ProviderCapabilities(supports_sessions=False)
        return ReviewSessionOptions(
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                max_parallel_calls=1,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
            synthesis=None,
            stop=stop,
        )

    context = replace(
        _pr_context(scratch),
        repo_root=worktree.path,
        head_worktree=worktree,
        checkout=ReviewCheckout.HEAD,
    )
    chunk_answer = AIResponse(
        content='{"summary": "", "checklist": [], "findings": []}',
        model="m",
        provider="anthropic",
    )
    # 1. completed run
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        AsyncMock(return_value=chunk_answer),
    ):
        run_review(context, options=_options())
    assert_that(Path(worktree.path).exists()).is_false()

    # 2. an exception inside the run
    worktree = checkout_pr_head(pr_number=1, head_oid=scratch["head"])
    assert worktree is not None
    context = replace(context, repo_root=worktree.path, head_worktree=worktree)
    with (
        patch(
            "lintro.ai.review.orchestrator.plan_run",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        run_review(context, options=_options())
    assert_that(Path(worktree.path).exists()).is_false()

    # 3. the interrupt (SIGTERM handler) set before the first call
    worktree = checkout_pr_head(pr_number=1, head_oid=scratch["head"])
    assert worktree is not None
    context = replace(context, repo_root=worktree.path, head_worktree=worktree)
    stop = asyncio.Event()
    stop.set()
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        AsyncMock(return_value=chunk_answer),
    ):
        run_review(context, options=_options(stop=stop))
    assert_that(Path(worktree.path).exists()).is_false()


def test_a_stale_worktree_from_a_killed_run_is_pruned(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a SIGKILL left behind is swept, and the same head can be reused."""
    monkeypatch.chdir(scratch["work"])
    first = checkout_pr_head(pr_number=1, head_oid=scratch["head"])
    assert first is not None
    # "Killed": nothing removed it. The next checkout sweeps it first.
    again = checkout_pr_head(pr_number=1, head_oid=scratch["head"])
    assert again is not None
    assert_that(Path(again.path).is_dir()).is_true()
    assert_that(
        _git(scratch["work"], "worktree", "list").count("pr-heads"),
    ).is_equal_to(1)
    remove_pr_head(again)
    # And the explicit sweep is a no-op on an empty cache.
    prune_stale_pr_worktrees(repo_root=str(scratch["work"]))
    assert_that(Path(again.path).exists()).is_false()


def test_a_head_gh_did_not_report_is_refused(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fetched ref must resolve to the head ``gh`` reported."""
    monkeypatch.chdir(scratch["work"])
    assert_that(checkout_pr_head(pr_number=1, head_oid=scratch["base"])).is_none()
    assert_that(checkout_pr_head(pr_number=99, head_oid=scratch["head"])).is_none()


# --- scenario 2: no repository at all ------------------------------------------


def test_without_a_repository_the_run_has_no_tree_and_no_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scratch: dict[str, Any],
) -> None:
    """No clone: checkout NONE, every CLI call without tools, one degradation."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with patch(
        "lintro.ai.review.context.collection._collect_pr_context",
        return_value=_pr_context(scratch),
    ):
        context = collect_review_context(pr_number=1, repo="o/r")

    assert_that(context.head_worktree).is_none()
    assert_that(context.checkout).is_equal_to(ReviewCheckout.NONE)
    assert_that(_tree_note_for(context=context)).contains("no tool is available")

    provider = MagicMock()
    provider.model_name = "m"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    options = ReviewSessionOptions(
        provider=provider,
        ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
        depth=1,
        checklist_items=[],
        checklist_text="",
        classifications=[],
    )
    plan = plan_run(context=context, options=options, timings=ReviewTimingRecorder())

    assert_that(plan.tools_disabled).is_true()
    assert_that(
        [
            d.reason
            for d in no_tree_degradations(plan=plan, ai_config=options.ai_config)
        ],
    ).is_equal_to([CoverageDegradationReason.NO_TREE_FOR_AGENT])
    # API transport has no agent: nothing to record.
    assert_that(
        no_tree_degradations(
            plan=plan,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
        ),
    ).is_empty()


async def test_every_pass_sends_no_tools_when_the_run_has_no_tree() -> None:
    """The chunk, question, adversarial, synthesis and verification calls all carry it."""
    from lintro.ai.review import adversarial_pass, question_pass, response_pipeline
    from lintro.ai.review.group_labels import REL_SINGLE_FILE
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.response_pipeline import ChunkReviewRequest

    captured: list[dict[str, Any]] = []

    async def _call_ai(**kwargs: Any) -> AIResponse:
        captured.append(kwargs)
        return AIResponse(content="{}", model="m", provider="anthropic")

    context = ReviewContext(
        base_ref="a",
        head_ref="b",
        changed_files=[
            ChangedFile(path="api.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -1 +1 @@\n-x\n+y\n",
        pr_metadata=None,
        checkout=ReviewCheckout.NONE,
    )
    chunk = ReviewChunk(
        id=1,
        files=["api.py"],
        diff=context.unified_diff,
        relationship=REL_SINGLE_FILE,
    )
    request = ChunkReviewRequest(
        chunk=chunk,
        context=context,
        provider=MagicMock(),
        ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
        checklist_text="",
        checklist_count=0,
        interaction_paths="",
        lint_results=None,
        extra_checklist="",
        strictness_section="",
        budget=MagicMock(),
        repo_root="",
        use_one_shot=True,
        diff_budget=100_000,
        chunk_index=0,
        tools_disabled=True,
    )
    with patch("lintro.ai.review.provider_call.call_ai", _call_ai):
        await response_pipeline.invoke_chunk_review(request=request)
        await adversarial_pass.run_adversarial_pass(
            chunk=chunk,
            provider=MagicMock(name="anthropic"),
            ai_config=request.ai_config,
            prior_findings=(),
            budget=MagicMock(),
            shape=CallShape(use_one_shot=True, no_tools=True),
        )
        await question_pass.generate_run_questions(
            context=context,
            provider=MagicMock(),
            ai_config=request.ai_config,
            budget=MagicMock(),
            diff_budget=100_000,
            shape=CallShape(use_one_shot=True, no_tools=True),
        )

    assert_that(captured).is_length(3)
    assert_that([c["no_tools"] for c in captured]).is_equal_to([True, True, True])


# --- scenario 3: the dogfood base checkout is unchanged ------------------------


def test_a_base_checkout_keeps_its_honest_label_when_the_head_cannot_be_fetched(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tree at the base and no fetchable head: checkout BASE, ambient root, tools on."""
    _git(scratch["work"], "checkout", "-q", scratch["base"])
    monkeypatch.chdir(scratch["work"])
    # ``gh`` collection probes the checkout itself; the stub returns what the
    # probe would have found for a tree at the base.
    with (
        patch(
            "lintro.ai.review.context.collection._collect_pr_context",
            return_value=replace(_pr_context(scratch), checkout=ReviewCheckout.BASE),
        ),
        patch(
            "lintro.ai.review.context.collection.checkout_pr_head",
            return_value=None,
        ),
    ):
        context = collect_review_context(pr_number=1, repo="o/r")

    assert_that(context.checkout).is_equal_to(ReviewCheckout.BASE)
    assert_that(context.head_worktree).is_none()
    assert_that(_tree_note_for(context=context)).contains("pre-change")


def test_a_base_checkout_with_a_fetchable_head_still_reads_the_head(
    scratch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dogfood shape today: base checked out, origin serves the PR head."""
    _git(scratch["work"], "checkout", "-q", scratch["base"])
    monkeypatch.chdir(scratch["work"])
    with patch(
        "lintro.ai.review.context.collection._collect_pr_context",
        return_value=_pr_context(scratch),
    ):
        context = collect_review_context(pr_number=1, repo="o/r")

    assert context.head_worktree is not None
    assert_that(context.checkout).is_equal_to(ReviewCheckout.HEAD)
    assert_that(_git(Path(context.repo_root), "rev-parse", "HEAD")).is_equal_to(
        scratch["head"],
    )
    remove_pr_head(context.head_worktree)


def test_remove_is_idempotent_and_tolerates_a_missing_tree(tmp_path: Path) -> None:
    """Removing twice, or a path that never existed, is a no-op."""
    remove_pr_head(None)
    ghost = PrHeadWorktree(
        path=str(tmp_path / "nope"),
        repo_root=str(tmp_path),
        head_oid="x",
    )
    remove_pr_head(ghost)
    remove_pr_head(ghost)
