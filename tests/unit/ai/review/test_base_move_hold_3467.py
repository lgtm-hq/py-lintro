"""A moved base never resolves a finding it pushed out of the diff (#3467).

Round one reports X at ``b.py:81`` (the pull request renamed ``a.py`` and
wrote the bug there) and X gets an inline thread. The base then advances to
include that commit, as a rebase onto a base that merged it does, and the
pull request renames ``b.py -> c.py`` with one appended line. Line 81 is still
the bug, but it is base content now: the diff gate drops the provider's
re-report there, and the round used to read the silence as a fix and resolve
the thread, ``(0, 0, 1)``. The same happened when a SIGTERM checkpoint was
resumed against the newer base.

These tests expect X held, ``(1, 0, 0)``, with its thread open, while a
finding whose line the pull request actually fixed after the rebase still
resolves. Every round runs through ``run_review`` on a scratch git repository,
in delta and in full mode, with the harness of #3413.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.incremental_coverage import (
    CheckpointStamp,
    write_incremental_coverage_part,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.state_store import load_ci_state
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import commit, git, lines

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]

_BUG = "bug_at_81 = True\n"
_FIX = "fixed_81 = 81\n"


def _with_line_81(text: str, *, line: str) -> str:
    """Return ``text`` with its 81st line replaced.

    Args:
        text: The file's text.
        line: The new line 81, newline included.

    Returns:
        The edited text.
    """
    rows = text.splitlines(keepends=True)
    rows[80] = line
    return "".join(rows)


def _pr_text() -> str:
    """The pull request's ``b.py``: its own first 100 lines, then the base's.

    Returns:
        The text, the bug at line 81.
    """
    head = lines(100, label="case").splitlines(keepends=True)
    tail = lines(300, label="base").splitlines(keepends=True)[100:]
    return _with_line_81("".join(head + tail), line=_BUG)


def _reported_on_b(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """Rename ``a.py -> b.py`` with the bug, and report X there with a thread.

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
    git(repo, "mv", "a.py", "b.py")
    (repo / "b.py").write_text(_pr_text())
    commit(repo, "rename a.py to b.py with the bug")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="b.py")],
        force_full=False,
    )
    assert_that(first.counts).is_equal_to((0, 1, 0))
    return repo, base, harness._with_thread(first.state)


def _rebase_and_rename(repo: Path, *, line_81: str) -> str:
    """Advance the base past round one's head and rename ``b.py -> c.py``.

    Args:
        repo: The repository at round one's head.
        line_81: Line 81 of ``c.py`` after the rename.

    Returns:
        The new base: round one's head, which the base branch now contains.
    """
    new_base = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "b.py", "c.py")
    text = _with_line_81(_pr_text(), line=line_81) + "# after rebase\n"
    (repo / "c.py").write_text(text)
    commit(repo, "rename b.py to c.py after the base advanced")
    return new_base


def _assert_held(*, round_: harness._Round, key: str, repo: Path) -> None:
    """X is carried open with its thread, and the bug is still in the code.

    Args:
        round_: The round after the base moved.
        key: X's key.
        repo: The repository at the round's head.
    """
    assert_that(round_.counts).is_equal_to((1, 0, 0))
    assert_that(round_.resolved_threads).is_empty()
    (record,) = round_.state.findings
    assert_that(record.key).is_equal_to(key)
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)
    assert_that(record.inline_comment_id).is_equal_to(harness._THREAD_ID)
    assert_that(record.unverified).is_true()
    bug_line = (repo / "c.py").read_text().splitlines(keepends=True)[80]
    assert_that(bug_line).is_equal_to(_BUG)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_rebase_that_drops_the_rename_holds_the_live_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """The bug became base content: its re-report is dropped, so X is held.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
    """
    repo, _, prior = _reported_on_b(tmp_path)
    new_base = _rebase_and_rename(repo, line_81=_BUG)

    after = harness._round(
        repo=repo,
        base=new_base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    assert_that(after.result.metadata.diff_gate.outside_diff).is_equal_to(1)
    _assert_held(round_=after, key=prior.findings[0].key, repo=repo)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_held_finding_stays_held_on_the_next_round(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A round after the held one, under the same base, does not resolve X.

    Its merge-base matches the previous round's, so a delta round reads only
    the new commit; X's own head still predates the base move.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for the later round.
    """
    repo, _, prior = _reported_on_b(tmp_path)
    new_base = _rebase_and_rename(repo, line_81=_BUG)
    held = harness._round(
        repo=repo,
        base=new_base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        force_full=False,
    )
    (repo / "c.py").write_text((repo / "c.py").read_text() + "# later\n")
    commit(repo, "touch the end of c.py again")

    later = harness._round(
        repo=repo,
        base=new_base,
        prior=held.state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    _assert_held(round_=later, key=prior.findings[0].key, repo=repo)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_checkpoint_resumed_against_a_newer_base_holds_the_live_finding(
    tmp_path: Path,
    force_full: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SIGTERM checkpoint written under the old base does not resolve X.

    The checkpoint holds no finished run, only the base it was written under,
    so the record's merge-base comes from that base.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
        monkeypatch: Pytest fixture.
    """
    repo, base, _ = _reported_on_b(tmp_path)
    context = harness._git_context(repo=repo, base=base)
    directory = tmp_path / "checkpoint"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(directory))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    write_incremental_coverage_part(
        collected=[
            ChunkReviewPartial(
                findings=(ReviewFinding(**harness._x(file="b.py")),),
                input_tokens=0,
                output_tokens=0,
                cost_estimate=0.0,
                files=("b.py",),
            ),
        ],
        resume=plan_resume(context=context, prior=None),
        context=context,
        prior_state=None,
        force_full=force_full,
        sequence=1,
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
        stamp=CheckpointStamp(pr_number=1),
    )
    loaded = load_ci_state(directory=directory, repo="o/r", pr_number=1)
    assert_that(loaded.base_sha).is_equal_to(base)
    prior = harness._with_thread(loaded)
    new_base = _rebase_and_rename(repo, line_81=_BUG)

    resumed = harness._round(
        repo=repo,
        base=new_base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    _assert_held(round_=resumed, key=prior.findings[0].key, repo=repo)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_finding_fixed_after_a_rebase_still_resolves(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """The pull request rewrites line 81 after the rebase: X resolves.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
    """
    repo, _, prior = _reported_on_b(tmp_path)
    new_base = _rebase_and_rename(repo, line_81=_FIX)

    fixed = harness._round(
        repo=repo,
        base=new_base,
        prior=prior,
        reported=[],
        force_full=force_full,
    )

    assert_that(fixed.counts).is_equal_to((0, 0, 1))
    assert_that(fixed.resolved_threads).is_equal_to((f"T{harness._THREAD_ID}",))
    (record,) = fixed.state.findings
    assert_that(record.status).is_equal_to(FindingStatus.RESOLVED)
    assert_that((repo / "c.py").read_text()).does_not_contain(_BUG)
