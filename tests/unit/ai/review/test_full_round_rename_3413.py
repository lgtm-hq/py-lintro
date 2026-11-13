"""A full round carries a renamed finding, as a delta round does (#3413).

#3403 made a delta round follow a rename: a prior open finding is moved to its
file's new path before matching, keeping its key and its thread. A full round
(``--full``, a rewritten branch, a moved base) skipped that step, so a finding
reported again after its file was renamed came back as one new finding plus
one resolved one, and the lifecycle resolved its original thread.

Every test drives a scratch git repository through ``run_review`` with the
changed-file metadata parsed from git, as a ``--pr`` run collects it; the
provider call is served canned findings, so nothing reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.context.diff_parse import parse_changed_files
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.lifecycle.state import departed_paths
from lintro.ai.review.lifecycle.threads import sync_addressed_lifecycle
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.lifecycle_sync_request import LifecycleSyncRequest
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky.assembly import advance_review_state
from lintro.ai.review.sticky.state import (
    matcher_relocations,
    matcher_reviewed_paths,
    matcher_reviewed_ranges,
)
from lintro.config.review_config import ReviewVerifyMode
from tests.unit.ai.review.test_carried_location_mapping_3403 import (
    _commit,
    _ThreadHost,
)
from tests.unit.ai.review.test_carried_reverify_3403 import _git, _lines
from tests.unit.ai.review.test_resume_fake_transport import _provider

_THREAD_ID = 3413


@dataclass(frozen=True)
class _Round:
    """One review round and what the GitHub path derives from it.

    Attributes:
        result: The round's result.
        match: The matcher's transitions, computed as ``github.py`` does.
        state: The state persisted after the round.
        resolved_threads: Thread node ids the lifecycle resolved.
    """

    result: ReviewResult
    match: FindingMatchResult
    state: ReviewState
    resolved_threads: tuple[str, ...]


def _x(*, file: str) -> dict[str, Any]:
    """X as the provider reports it on ``file``, line 81.

    Args:
        file: The file.

    Returns:
        The finding as the provider would return it.
    """
    return {
        "severity": "P2",
        "category": "logic-bug",
        "file": file,
        "line": 81,
        "title": "X",
        "description": "Reproducible bug",
        "cause": "bad logic",
        "fix": "repair the logic",
        "confidence": "high",
    }


def _git_context(*, repo: Path, base: str) -> ReviewContext:
    """The ``--pr`` context at HEAD, changed files parsed from git.

    Args:
        repo: The repository.
        base: The base commit.

    Returns:
        The context, rename metadata included.
    """
    head = _git(repo, "rev-parse", "HEAD")
    span = f"{base}..{head}"
    changed = parse_changed_files(
        name_status=_git(repo, "diff", "--name-status", "-z", "-M", span) + "\0",
        numstat=_git(repo, "diff", "--numstat", "-z", "-M", span) + "\0",
    )
    return ReviewContext(
        base_ref=base,
        head_ref=head,
        changed_files=changed,
        unified_diff=_git(repo, "diff", "-M", span) + "\n",
        repo_root=str(repo),
        checkout=ReviewCheckout.HEAD,
        pr_metadata=PRMetadata(title="t", body="", number=1, repo="o/r"),
    )


def _round(
    *,
    repo: Path,
    base: str,
    prior: ReviewState | None,
    reported: list[dict[str, Any]],
    force_full: bool,
) -> _Round:
    """Run one real review round and its thread lifecycle.

    Args:
        repo: The repository at the round's head.
        base: The base commit.
        prior: State carried into the round.
        reported: Findings the provider returns.
        force_full: ``--full``.

    Returns:
        The round.
    """
    context = _git_context(repo=repo, base=base)
    head = context.head_ref

    def canned(**kwargs: Any) -> AIResponse:
        """Serve the round's canned findings, whatever the call asked.

        Args:
            **kwargs: Ignored provider call arguments.

        Returns:
            The canned response.
        """
        return AIResponse(
            content=json.dumps(
                {"summary": "Canned review", "checklist": [], "findings": reported},
            ),
            model="fake-model",
            provider="fake",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.01,
        )

    with patch("lintro.ai.review.provider_call.call_ai", side_effect=canned):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    review_generated_questions=False,
                    transport=AITransport.API,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
                prior_state=prior,
                enforce_cost_cap=False,
                verify=ReviewVerifyMode.OFF,
                force_full=force_full,
            ),
        )
    # A verified round is one the reuse gate (#2867) lets a delta anchor on.
    result = replace(
        result,
        metadata=replace(result.metadata, model_verification="verified"),
    )
    departed = departed_paths(context=context)
    match = match_findings(
        previous=prior,
        findings=result.findings,
        round_number=(prior or ReviewState()).next_round,
        head_sha=head,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=departed,
        reviewed_ranges=matcher_reviewed_ranges(result=result),
        relocations=matcher_relocations(result=result),
    )
    host = _ThreadHost(
        bodies={
            record.inline_comment_id: f"**{record.title}** on {record.file}"
            for record in (prior.findings if prior else ())
            if record.inline_comment_id is not None
        },
    )
    sync_addressed_lifecycle(
        reporter=host,
        request=LifecycleSyncRequest(
            comment_bodies=dict(host.bodies),
            head_sha=head,
            round_number=(prior or ReviewState()).next_round,
            resolved=match.resolved,
        ),
    )
    state = advance_review_state(
        request=StickyRequest(
            result=result,
            prior_state=prior,
            head_sha=head,
            departed_paths=departed,
        ),
    )
    return _Round(
        result=result,
        match=match,
        state=state,
        resolved_threads=tuple(host.resolved),
    )


def _first_round(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """X is reported at ``a.py:81`` and gets an inline thread.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository, the base commit and the state after round one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(_lines(300, label="base"))
    base = _commit(repo, "base")
    (repo / "a.py").write_text(_lines(300, label="case"))
    _commit(repo, "first")
    first = _round(
        repo=repo,
        base=base,
        prior=None,
        reported=[_x(file="a.py")],
        force_full=False,
    )
    (x,) = first.state.findings
    state = replace(
        first.state,
        findings=(replace(x, inline_comment_id=_THREAD_ID),),
    )
    return repo, base, state


def _counts(match: FindingMatchResult) -> tuple[int, int, int]:
    """Return ``(carried, new, resolved)``.

    Args:
        match: The matcher's transitions.

    Returns:
        The counts.
    """
    return len(match.carried), len(match.new), len(match.resolved)


def test_a_full_round_after_a_rename_carries_the_finding(tmp_path: Path) -> None:
    """X moved ``a.py`` → ``b.py`` and is reported again: carried, thread kept."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    _git(repo, "mv", "a.py", "b.py")
    _commit(repo, "rename")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="b.py")],
        force_full=True,
    )

    assert_that(second.result.metadata.delta_reason).is_equal_to(
        DeltaReason.EXPLICIT_FULL,
    )
    assert_that(_counts(second.match)).is_equal_to((1, 0, 0))
    (carried,) = second.match.carried
    assert_that((carried.key, carried.inline_comment_id, carried.file)).is_equal_to(
        (original.key, _THREAD_ID, "b.py"),
    )
    assert_that(second.resolved_threads).is_empty()
    (stored,) = second.state.findings
    assert_that((stored.key, stored.status, stored.file)).is_equal_to(
        (original.key, FindingStatus.OPEN, "b.py"),
    )


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        pytest.param([_x(file="b.py")], (1, 0, 0), id="reported-again"),
        pytest.param([], (0, 0, 1), id="fixed"),
    ],
)
def test_delta_and_full_rounds_agree_on_a_rename(
    tmp_path: Path,
    reported: list[dict[str, Any]],
    expected: tuple[int, int, int],
) -> None:
    """The same history gives the same carried, new and resolved counts."""
    repo, base, prior = _first_round(tmp_path)
    _git(repo, "mv", "a.py", "b.py")
    _commit(repo, "rename")

    rounds = {
        force_full: _round(
            repo=repo,
            base=base,
            prior=prior,
            reported=reported,
            force_full=force_full,
        )
        for force_full in (False, True)
    }

    assert_that(rounds[False].result.metadata.delta_reason).is_equal_to(
        DeltaReason.DELTA,
    )
    assert_that(_counts(rounds[False].match)).is_equal_to(expected)
    assert_that(_counts(rounds[True].match)).is_equal_to(expected)
    assert_that(
        [record.key for record in rounds[True].match.resolved],
    ).is_equal_to([record.key for record in rounds[False].match.resolved])


def test_a_full_round_after_a_chained_rename_carries_the_finding(
    tmp_path: Path,
) -> None:
    """X follows ``a.py`` → ``b.py`` → ``c.py`` across full rounds."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    _git(repo, "mv", "a.py", "b.py")
    _commit(repo, "rename to b")
    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="b.py")],
        force_full=True,
    )
    _git(repo, "mv", "b.py", "c.py")
    _commit(repo, "rename to c")

    third = _round(
        repo=repo,
        base=base,
        prior=second.state,
        reported=[_x(file="c.py")],
        force_full=True,
    )

    assert_that(_counts(third.match)).is_equal_to((1, 0, 0))
    (carried,) = third.match.carried
    assert_that((carried.key, carried.inline_comment_id, carried.file)).is_equal_to(
        (original.key, _THREAD_ID, "c.py"),
    )
    assert_that(second.resolved_threads + third.resolved_threads).is_empty()


def test_a_full_round_over_two_renames_at_once_carries_the_finding(
    tmp_path: Path,
) -> None:
    """X follows ``a.py`` → ``b.py`` → ``c.py`` pushed between two rounds."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    _git(repo, "mv", "a.py", "b.py")
    _commit(repo, "rename to b")
    _git(repo, "mv", "b.py", "c.py")
    _commit(repo, "rename to c")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="c.py")],
        force_full=True,
    )

    assert_that(_counts(second.match)).is_equal_to((1, 0, 0))
    (carried,) = second.match.carried
    assert_that((carried.key, carried.inline_comment_id, carried.file)).is_equal_to(
        (original.key, _THREAD_ID, "c.py"),
    )
    assert_that(second.resolved_threads).is_empty()
