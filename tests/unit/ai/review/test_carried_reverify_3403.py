"""Open findings are re-judged on a delta round, not carried unread (#3403).

The fixtures mirror two real pull requests. On #3374 a P3 test gap at
``tests/unit/ai/review/test_lint_bridge.py:81`` was fixed by appending the
requested tests further down the file. On #3391 a P2 at
``lintro/utils/execution/fix_retry.py:96`` was fixed by a change to the merge
block below the loop. Neither finding was posted inline. In both cases the
delta round showed only the new lines, the finding's own line fell outside
every delta hunk, and the matcher carried the finding forward as "—"
(unchanged) round after round. On #3391 that held the verdict at "Changes
requested".

Every test drives a scratch git repository: no network, no provider.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - scratch repositories are driven with fixed git argv
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.delta import apply_delta_hunks, delta_hunks
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.finding_matcher import review_finding_from_record
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky.assembly import (
    advance_review_state,
    build_sticky_comment,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}

#: The #3391 finding: open since round 4, never posted inline.
_FIX_RETRY = "lintro/utils/execution/fix_retry.py"
_FIX_RETRY_TITLE = "Execution-failed retry pass still gets fabricated fix counts"

#: The #3374 finding: a P3 test gap, never posted inline.
_BRIDGE_TEST = "tests/unit/ai/review/test_lint_bridge.py"
_BRIDGE_TITLE = "Bridge black-skip branch has no test coverage"


def _git(cwd: Path, *args: str) -> str:
    """Run git in a scratch repository.

    Args:
        cwd: The repository.
        *args: Git arguments.

    Returns:
        Stripped stdout.
    """
    return subprocess.run(  # nosec B603 B607 - fixed argv over a scratch repository
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        env={**os.environ, **_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _lines(count: int, *, label: str) -> str:
    """Return ``count`` numbered filler lines.

    Args:
        count: How many lines.
        label: Prefix that keeps each file's text distinct.

    Returns:
        The text, newline-terminated.
    """
    return "".join(f"{label}_{index} = {index}\n" for index in range(1, count + 1))


@dataclass(frozen=True)
class _Pr:
    """A scratch pull request with one fix pushed after the prior round.

    Attributes:
        repo: The repository.
        base: The base commit.
        prior: The head the prior round reviewed.
        head: The head after the fix.
    """

    repo: Path
    base: str
    prior: str
    head: str


@pytest.fixture
def pr(tmp_path: Path) -> _Pr:
    """Build the two PR shapes: each fix lands far below its finding's line.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository and its commits.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    fix_retry = repo / _FIX_RETRY
    bridge = repo / _BRIDGE_TEST
    fix_retry.parent.mkdir(parents=True)
    bridge.parent.mkdir(parents=True)
    fix_retry.write_text(_lines(200, label="retry"))
    bridge.write_text(_lines(200, label="bridge"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    # The PR's round-4 head: the whole file is reworked.
    _git(repo, "checkout", "-q", "-b", "pr")
    fix_retry.write_text(_lines(200, label="loop"))
    bridge.write_text(_lines(200, label="case"))
    _git(repo, "commit", "-q", "-am", "round 4")
    prior = _git(repo, "rev-parse", "HEAD")
    # The fixes: #3391 widens the merge block at line 180, #3374 appends tests.
    text = fix_retry.read_text().splitlines(keepends=True)
    text[179] = "merge_block = is_incomplete(result)\n"
    fix_retry.write_text("".join(text))
    bridge.write_text(bridge.read_text() + "def test_black_skipped(): ...\n")
    _git(repo, "commit", "-q", "-am", "fix")
    head = _git(repo, "rev-parse", "HEAD")
    return _Pr(repo=repo, base=base, prior=prior, head=head)


def _record(*, file: str, line: int, title: str, severity: Severity) -> FindingRecord:
    """An open record with no inline thread, as on #3374 and #3391.

    Args:
        file: The file.
        line: The finding's line.
        title: The finding's title.
        severity: The finding's severity.

    Returns:
        The record.
    """
    category = "logic-bug" if severity is Severity.P2 else "test-gap"
    return FindingRecord(
        fingerprint=fingerprint_for(file=file, category=category, title=title),
        severity=severity,
        category=category,
        title=title,
        file=file,
        line=line,
        status=FindingStatus.OPEN,
        since_round=4,
        inline_comment_id=None,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def _prior_state(*, prior_head: str) -> ReviewState:
    """State after round 4: two open, unthreaded findings.

    Args:
        prior_head: The head round 4 reviewed.

    Returns:
        The state.
    """
    return ReviewState(
        findings=(
            _record(
                file=_FIX_RETRY,
                line=96,
                title=_FIX_RETRY_TITLE,
                severity=Severity.P2,
            ),
            _record(
                file=_BRIDGE_TEST,
                line=81,
                title=_BRIDGE_TITLE,
                severity=Severity.P3,
            ),
        ),
        runs=(RunRecord(identity=RunIdentity(round=4, sha=prior_head)),),
        head_sha=prior_head,
    )


def _plan_round(
    *,
    pr: _Pr,
    prior: ReviewState,
) -> tuple[tuple[str, int, int], ...]:
    """Run the delta step of round 5 and return the ranges it recorded.

    Args:
        pr: The scratch pull request.
        prior: State carried into round 5.

    Returns:
        The ``(path, start, end)`` ranges the round read, as the run planner
        records them on the result.
    """
    whole = _git(pr.repo, "diff", f"{pr.base}..{pr.head}")
    per_file = split_unified_diff_by_file(unified_diff=whole)
    chunks = [
        ReviewChunk(id=index, files=[path], diff=diff, relationship=REL_SINGLE_FILE)
        for index, (path, diff) in enumerate(sorted(per_file.items()), start=1)
    ]
    hunks = delta_hunks(
        repo_root=str(pr.repo),
        since_sha=pr.prior,
        head_sha=pr.head,
        pr_paths=list(per_file),
    )
    assert hunks is not None
    applied = apply_delta_hunks(
        chunks=chunks,
        hunks=hunks,
        since_sha=pr.prior,
        prior=prior,
    )
    return applied.reviewed_ranges


def _round_result(
    *,
    reviewed_paths: tuple[str, ...],
    reviewed_ranges: tuple[tuple[str, int, int], ...],
) -> ReviewResult:
    """Round 5's result: the reviewer re-reported nothing.

    Args:
        reviewed_paths: Files the round read.
        reviewed_ranges: Delta ranges the round recorded.

    Returns:
        The result.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="anthropic",
            context_window=200_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=len(reviewed_paths),
            files_total=len(reviewed_paths),
            checklist_items=0,
            reviewed_paths=reviewed_paths,
            reviewed_ranges=reviewed_ranges,
        ),
        summary="Nothing blocks the merge.",
        findings=(),
    )


def _sticky_request(
    *,
    prior: ReviewState,
    result: ReviewResult,
    head: str,
) -> StickyRequest:
    """Build round 5's sticky request.

    Args:
        prior: State carried into round 5.
        result: Round 5's result.
        head: Round 5's head.

    Returns:
        The request.
    """
    return StickyRequest(
        result=result,
        prior_state=prior,
        head_sha=head,
        repo="lgtm-hq/py-lintro",
        pr_number=3391,
    )


def test_a_fixed_unthreaded_finding_is_reported_fixed_next_round(pr: _Pr) -> None:
    """A fix below the finding's line no longer leaves it carried as unchanged."""
    prior = _prior_state(prior_head=pr.prior)
    ranges = _plan_round(pr=pr, prior=prior)
    # Both files hold an open finding the delta would not show: read whole.
    assert_that({path for path, _, _ in ranges}).does_not_contain(
        _FIX_RETRY,
        _BRIDGE_TEST,
    )
    request = _sticky_request(
        prior=prior,
        result=_round_result(
            reviewed_paths=(_BRIDGE_TEST, _FIX_RETRY),
            reviewed_ranges=ranges,
        ),
        head=pr.head,
    )

    body = build_sticky_comment(request=request)
    state = advance_review_state(request=request)

    assert_that(body).contains("## 🔎 Lintro Review — ✅ Ready")
    assert_that(body).contains("0 open · 2 fixed this round")
    assert_that(body).contains(f"~~{_FIX_RETRY_TITLE}~~")
    statuses = {record.title: record.status for record in state.findings}
    assert_that(statuses).is_equal_to(
        {
            _FIX_RETRY_TITLE: FindingStatus.RESOLVED,
            _BRIDGE_TITLE: FindingStatus.RESOLVED,
        },
    )
    assert_that({record.resolved_sha for record in state.findings}).is_equal_to(
        {pr.head},
    )
    assert_that(state.runs[-1].outcome.verdict).is_equal_to(ReviewVerdict.READY)


def test_a_finding_inside_the_delta_still_narrows_its_file(pr: _Pr) -> None:
    """Narrowing is kept when the delta already shows every open line."""
    prior = _prior_state(prior_head=pr.prior)
    shown = replace(prior.findings[0], line=180)
    ranges = _plan_round(pr=pr, prior=replace(prior, findings=(shown,)))
    narrowed = {path for path, _, _ in ranges}
    assert_that(narrowed).contains(_FIX_RETRY)
    # The other file has no open finding, so its delta is used as before.
    assert_that(narrowed).contains(_BRIDGE_TEST)


def test_an_unread_carried_finding_is_labelled_and_cannot_request_changes(
    pr: _Pr,
) -> None:
    """A finding nothing re-read is labelled and does not set the verdict alone."""
    prior = _prior_state(prior_head=pr.prior)
    # The round read neither file (a cost cap stopped it, for instance), so
    # nothing re-judged the open P2.
    request = _sticky_request(
        prior=prior,
        result=_round_result(reviewed_paths=("other.py",), reviewed_ranges=()),
        head=pr.head,
    )

    body = build_sticky_comment(request=request)
    state = advance_review_state(request=request)

    assert_that(body).does_not_contain("Changes requested")
    assert_that(body).contains("## 🔎 Lintro Review — 🟡 Nits only")
    row = next(line for line in body.splitlines() if _FIX_RETRY_TITLE in line)
    assert_that(row).starts_with("| ⏸ not re-verified |")
    # The record stays open in the persisted state: carried, not fixed.
    statuses = {record.title: record.status for record in state.findings}
    assert_that(statuses[_FIX_RETRY_TITLE]).is_equal_to(FindingStatus.OPEN)
    assert_that(state.runs[-1].outcome.verdict).is_equal_to(ReviewVerdict.NITS_ONLY)


def test_a_re_reported_finding_still_requests_changes(pr: _Pr) -> None:
    """A finding the round re-asserts keeps driving the verdict and its "—" cell."""
    prior = _prior_state(prior_head=pr.prior)
    still_open = prior.findings[0]
    result = replace(
        _round_result(reviewed_paths=(_BRIDGE_TEST, _FIX_RETRY), reviewed_ranges=()),
        findings=(review_finding_from_record(record=still_open),),
    )
    request = _sticky_request(prior=prior, result=result, head=pr.head)

    body = build_sticky_comment(request=request)
    state = advance_review_state(request=request)

    assert_that(body).contains("## 🔎 Lintro Review — ⚠️ Changes requested")
    row = next(line for line in body.splitlines() if _FIX_RETRY_TITLE in line)
    assert_that(row).starts_with("| — |")
    assert_that(state.runs[-1].outcome.verdict).is_equal_to(
        ReviewVerdict.CHANGES_REQUESTED,
    )
