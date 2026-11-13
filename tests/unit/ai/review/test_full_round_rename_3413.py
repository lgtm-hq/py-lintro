"""A full round carries a renamed finding, as a delta round does (#3413).

#3403 made a delta round follow a rename: a prior open finding is moved to its
file's new path before matching, keeping its key and its thread. A full round
(``--full``, a rewritten branch, a moved base) skipped that step, so a finding
reported again after its file was renamed came back as one new finding plus
one resolved one, and the lifecycle resolved its original thread.

When git cannot reach the head a finding was recorded at (a rewritten branch
in the workflow's shallow checkout), the pull request's rename metadata moves
it instead; when nothing places it, it is held open rather than resolved.

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
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.lifecycle.state import departed_paths
from lintro.ai.review.lifecycle.threads import sync_addressed_lifecycle
from lintro.ai.review.models.delta_plan import LOCATION_UNCHECKED, LOCATION_UNMAPPED
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.lifecycle_sync_request import LifecycleSyncRequest
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky.assembly import (
    advance_review_state,
    build_sticky_comment,
)
from lintro.ai.review.sticky.state import (
    matcher_relocations,
    matcher_reviewed_paths,
    matcher_reviewed_ranges,
)
from lintro.config.review_config import ReviewVerifyMode
from tests.unit.ai.review.scratch_repo_fixtures import (
    ThreadHost,
    commit,
    fake_provider,
    git,
    lines,
)

_THREAD_ID = 3413

#: A head no repository holds: the line_sha of a force-pushed-away commit.
_GONE = "f" * 40


@dataclass(frozen=True)
class _Round:
    """One review round and what the GitHub path derives from it.

    Attributes:
        result: The round's result.
        match: The matcher's transitions, computed as ``github.py`` does.
        state: The state persisted after the round.
        sticky: The rendered sticky comment.
        resolved_threads: Thread node ids the lifecycle resolved.
    """

    result: ReviewResult
    match: FindingMatchResult
    state: ReviewState
    sticky: str
    resolved_threads: tuple[str, ...]

    @property
    def counts(self) -> tuple[int, int, int]:
        """Return ``(carried, new, resolved)``.

        Returns:
            The counts.
        """
        match = self.match
        return len(match.carried), len(match.new), len(match.resolved)

    @property
    def verdict(self) -> ReviewVerdict:
        """Return the verdict the round recorded.

        Returns:
            The verdict of the last run record.
        """
        return self.state.runs[-1].outcome.verdict


def _x(*, file: str, line: int = 81) -> dict[str, Any]:
    """X as the provider reports it.

    Args:
        file: The file.
        line: The line.

    Returns:
        The finding as the provider would return it.
    """
    return {
        "severity": "P2",
        "category": "logic-bug",
        "file": file,
        "line": line,
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
    head = git(repo, "rev-parse", "HEAD")
    span = f"{base}..{head}"
    changed = parse_changed_files(
        name_status=git(repo, "diff", "--name-status", "-z", "-M", span) + "\0",
        numstat=git(repo, "diff", "--numstat", "-z", "-M", span) + "\0",
    )
    return ReviewContext(
        base_ref=base,
        head_ref=head,
        changed_files=changed,
        unified_diff=git(repo, "diff", "-M", span) + "\n",
        repo_root=str(repo),
        checkout=ReviewCheckout.HEAD,
        pr_metadata=PRMetadata(title="t", body="", number=1, repo="o/r"),
    )


def _canned(reported: list[dict[str, Any]]) -> AIResponse:
    """The provider's answer for a round.

    Args:
        reported: Findings the provider returns.

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


def _round(
    *,
    repo: Path,
    base: str,
    prior: ReviewState | None,
    reported: list[dict[str, Any]],
    force_full: bool,
) -> _Round:
    """Run one real review round, its thread lifecycle and its sticky.

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
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        return_value=_canned(reported),
    ):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=fake_provider(),
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
    round_number = (prior or ReviewState()).next_round
    match = match_findings(
        previous=prior,
        findings=result.findings,
        round_number=round_number,
        head_sha=head,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=departed,
        reviewed_ranges=matcher_reviewed_ranges(result=result),
        relocations=matcher_relocations(result=result),
    )
    host = ThreadHost(
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
            round_number=round_number,
            resolved=match.resolved,
        ),
    )
    request = StickyRequest(
        result=result,
        prior_state=prior,
        head_sha=head,
        departed_paths=departed,
    )
    return _Round(
        result=result,
        match=match,
        state=advance_review_state(request=request),
        sticky=build_sticky_comment(request=request),
        resolved_threads=tuple(host.resolved),
    )


def _with_thread(state: ReviewState) -> ReviewState:
    """Give the state's only finding an inline thread.

    Args:
        state: State holding one finding.

    Returns:
        The state, its finding carrying :data:`_THREAD_ID`.
    """
    (x,) = state.findings
    return replace(state, findings=(replace(x, inline_comment_id=_THREAD_ID),))


def _first_round(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """X is reported at ``a.py:81`` and gets an inline thread.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository, the base commit and the state after round one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    base = commit(repo, "base")
    (repo / "a.py").write_text(lines(300, label="case"))
    commit(repo, "first")
    first = _round(
        repo=repo,
        base=base,
        prior=None,
        reported=[_x(file="a.py")],
        force_full=False,
    )
    return repo, base, _with_thread(first.state)


def _assert_carried_with_thread(
    *,
    round_: _Round,
    key: str,
    file: str,
) -> None:
    """X is carried under its key and thread, open in state and on the sticky.

    Args:
        round_: The round.
        key: X's original key.
        file: The path X should now be on.
    """
    assert_that(round_.counts).is_equal_to((1, 0, 0))
    (carried,) = round_.match.carried
    assert_that((carried.key, carried.inline_comment_id, carried.file)).is_equal_to(
        (key, _THREAD_ID, file),
    )
    assert_that(round_.resolved_threads).is_empty()
    stored = next(record for record in round_.state.findings if record.key == key)
    assert_that((stored.status, stored.file)).is_equal_to((FindingStatus.OPEN, file))
    # One open P2 is still owed: the verdict and the sticky both say so.
    assert_that(round_.verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)
    (row,) = (
        line for line in round_.sticky.splitlines() if f"lintro-finding:{key}" in line
    )
    assert_that(row).contains(f"`{file}:")
    assert_that(row).does_not_contain("~~")


def test_a_full_round_after_a_rename_carries_the_finding(tmp_path: Path) -> None:
    """X moved ``a.py`` → ``b.py`` and is reported again: carried, thread kept."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename")

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
    _assert_carried_with_thread(round_=second, key=original.key, file="b.py")


@pytest.mark.parametrize(
    ("reported", "expected", "verdict"),
    [
        pytest.param(
            [_x(file="b.py")],
            (1, 0, 0),
            ReviewVerdict.CHANGES_REQUESTED,
            id="reported-again",
        ),
        pytest.param([], (0, 0, 1), ReviewVerdict.READY, id="fixed"),
    ],
)
def test_delta_and_full_rounds_agree_on_a_rename(
    tmp_path: Path,
    reported: list[dict[str, Any]],
    expected: tuple[int, int, int],
    verdict: ReviewVerdict,
) -> None:
    """The same history gives the same counts, verdict and sticky outcome."""
    repo, base, prior = _first_round(tmp_path)
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename")

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
    for round_ in rounds.values():
        assert_that(round_.counts).is_equal_to(expected)
        assert_that(round_.verdict).is_equal_to(verdict)
        assert_that("~~X~~" in round_.sticky).is_equal_to(expected[2] == 1)
    assert_that(
        [record.key for record in rounds[True].match.resolved],
    ).is_equal_to([record.key for record in rounds[False].match.resolved])


def test_a_full_round_after_a_chained_rename_carries_the_finding(
    tmp_path: Path,
) -> None:
    """X follows ``a.py`` → ``b.py`` → ``c.py`` across full rounds."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename to b")
    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="b.py")],
        force_full=True,
    )
    git(repo, "mv", "b.py", "c.py")
    commit(repo, "rename to c")

    third = _round(
        repo=repo,
        base=base,
        prior=second.state,
        reported=[_x(file="c.py")],
        force_full=True,
    )

    _assert_carried_with_thread(round_=second, key=original.key, file="b.py")
    _assert_carried_with_thread(round_=third, key=original.key, file="c.py")


def test_a_full_round_over_two_renames_at_once_carries_the_finding(
    tmp_path: Path,
) -> None:
    """X follows ``a.py`` → ``b.py`` → ``c.py`` pushed between two rounds."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename to b")
    git(repo, "mv", "b.py", "c.py")
    commit(repo, "rename to c")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="c.py")],
        force_full=True,
    )

    _assert_carried_with_thread(round_=second, key=original.key, file="c.py")


def test_a_full_round_moves_an_unrenamed_finding_to_the_head_line(
    tmp_path: Path,
) -> None:
    """Ten lines inserted above X: the full round stores X at line 91, not 81."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    path = repo / "a.py"
    path.write_text(lines(10, label="inserted") + path.read_text())
    head = commit(repo, "insert above X")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="a.py", line=91)],
        force_full=True,
    )

    assert_that(second.result.metadata.relocated_findings).is_equal_to(
        ((original.key, "a.py", 91, head),),
    )
    _assert_carried_with_thread(round_=second, key=original.key, file="a.py")
    (stored,) = second.state.findings
    assert_that((stored.line, stored.line_sha)).is_equal_to((91, head))


def _rewritten_shallow_pr(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """The workflow's checkout after a history rewrite that renamed X's file.

    X was reported at ``a.py:81`` on a head the branch then force-pushed
    away; the new head renames ``a.py`` to ``b.py`` (git reports ``R099``).
    The review runs in a depth-1 clone of the trusted branch plus an exact
    fetch of the pull-request head, so the old head is not in the clone.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The clone, the base commit and the prior state.
    """
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    base = commit(repo, "base")
    text = (repo / "a.py").read_text().splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "a.py").write_text("".join(text))
    commit(repo, "reviewed feature")
    first = _round(
        repo=repo,
        base=base,
        prior=None,
        reported=[_x(file="a.py")],
        force_full=False,
    )
    git(repo, "reset", "-q", "--hard", base)
    git(repo, "mv", "a.py", "b.py")
    (repo / "b.py").write_text("".join(text))
    head = commit(repo, "rewritten feature plus rename")
    git(repo, "update-ref", "refs/heads/trusted", base)
    git(repo, "update-ref", "refs/pull/1/head", head)
    clone = tmp_path / "workflow"
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth",
        "1",
        "--branch",
        "trusted",
        repo.as_uri(),
        str(clone),
    )
    git(clone, "fetch", "-q", "--no-tags", "origin", "+refs/pull/1/head:refs/pr")
    git(clone, "checkout", "-q", "--detach", "refs/pr")
    return clone, base, _with_thread(first.state)


@pytest.mark.parametrize(
    ("force_full", "reason"),
    [
        pytest.param(False, DeltaReason.NOT_ANCESTOR, id="automatic"),
        pytest.param(True, DeltaReason.EXPLICIT_FULL, id="forced-full"),
    ],
)
def test_a_rename_after_a_history_rewrite_follows_the_pr_metadata(
    tmp_path: Path,
    force_full: bool,
    reason: DeltaReason,
) -> None:
    """X's head is gone from the clone; the PR's rename moves it to ``b.py``."""
    clone, base, prior = _rewritten_shallow_pr(tmp_path)
    (original,) = prior.findings
    assert_that(
        git(clone, "rev-parse", "--is-shallow-repository"),
    ).is_equal_to("true")

    second = _round(
        repo=clone,
        base=base,
        prior=prior,
        reported=[_x(file="b.py")],
        force_full=force_full,
    )

    assert_that(second.result.metadata.delta_reason).is_equal_to(reason)
    _assert_carried_with_thread(round_=second, key=original.key, file="b.py")
    (stored,) = second.state.findings
    assert_that(stored.match_fingerprint).is_equal_to(
        fingerprint_for(file="b.py", category="logic-bug", title="X"),
    )


def test_an_unreachable_head_on_a_deleted_file_is_not_a_fix(
    tmp_path: Path,
) -> None:
    """X's head is gone and ``a.py`` left the diff: X is held, not resolved."""
    repo, base, prior = _first_round(tmp_path)
    (x,) = prior.findings
    prior = replace(prior, findings=(replace(x, line_sha=_GONE),))
    (repo / "other.py").write_text(lines(5, label="other"))
    git(repo, "rm", "-q", "a.py")
    commit(repo, "delete a.py")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=True,
    )

    assert_that(second.result.metadata.relocated_findings).is_equal_to(
        ((x.key, "a.py", 81, LOCATION_UNMAPPED),),
    )
    assert_that(second.counts).is_equal_to((1, 0, 0))
    assert_that(second.resolved_threads).is_empty()
    (stored,) = second.state.findings
    # Held where it was stored, so the next round is held the same way.
    assert_that(
        (stored.status, stored.file, stored.line_sha, stored.unverified),
    ).is_equal_to((FindingStatus.OPEN, "a.py", _GONE, True))
    assert_that(second.verdict).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)
    assert_that(second.sticky).contains("⏸ not re-verified")
    assert_that(second.sticky).does_not_contain("~~X~~")


def test_an_unreachable_head_on_a_file_read_whole_is_judged(
    tmp_path: Path,
) -> None:
    """X's head is gone but ``a.py`` is still in the diff and read: X resolves."""
    repo, base, prior = _first_round(tmp_path)
    (x,) = prior.findings
    prior = replace(prior, findings=(replace(x, line_sha=_GONE),))
    (repo / "a.py").write_text(lines(300, label="fixed"))
    commit(repo, "rewrite a.py")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=True,
    )

    assert_that(second.counts).is_equal_to((0, 0, 1))
    assert_that(second.resolved_threads).is_equal_to((f"T{_THREAD_ID}",))
    assert_that(second.verdict).is_equal_to(ReviewVerdict.READY)
    assert_that(second.sticky).contains("~~X~~")


def test_a_finding_beyond_the_mapping_cap_is_never_resolved(
    tmp_path: Path,
) -> None:
    """Over the cap, X is not mapped and stays open even though it is absent."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    (repo / "a.py").write_text(lines(300, label="fixed"))
    commit(repo, "rewrite a.py")

    with patch("lintro.ai.review.delta_lines.MAX_MAPPED_FINDINGS", 0):
        second = _round(
            repo=repo,
            base=base,
            prior=prior,
            reported=[],
            force_full=True,
        )

    assert_that(second.result.metadata.relocated_findings).is_equal_to(
        ((original.key, "a.py", 81, LOCATION_UNCHECKED),),
    )
    assert_that(second.counts).is_equal_to((1, 0, 0))
    assert_that(second.resolved_threads).is_empty()
    (stored,) = second.state.findings
    assert_that((stored.status, stored.unverified)).is_equal_to(
        (FindingStatus.OPEN, True),
    )
    assert_that(second.sticky).contains("⏸ not re-verified")


def test_a_renamed_finding_pairs_open_over_a_resolved_sibling(
    tmp_path: Path,
) -> None:
    """``b.py`` holds a resolved X; the renamed open X is the one carried."""
    repo, base, prior = _first_round(tmp_path)
    (original,) = prior.findings
    sibling = replace(
        original,
        fingerprint=fingerprint_for(file="b.py", category="logic-bug", title="X"),
        file="b.py",
        status=FindingStatus.RESOLVED,
        resolved_sha=original.line_sha,
        resolved_round=1,
        inline_comment_id=None,
    )
    prior = replace(prior, findings=(original, sibling))
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename")

    second = _round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[_x(file="b.py")],
        force_full=True,
    )

    assert_that(second.match.regressed).is_empty()
    _assert_carried_with_thread(round_=second, key=original.key, file="b.py")
    kept = next(r for r in second.state.findings if r.key == sibling.key)
    assert_that(kept.status).is_equal_to(FindingStatus.RESOLVED)
