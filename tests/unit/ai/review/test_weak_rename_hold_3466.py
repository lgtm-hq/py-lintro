"""A low-similarity rename alone never resolves a finding (#3466).

Git pairs a deleted file with an added one by content similarity. Round one
reports X on ``a.py`` and round two carries it across ``a.py -> b.py``. Round
three adds a new, unrelated ``a.py`` about two thirds like ``b.py``, renames it
to ``c.py``, and rewrites ``b.py`` into ``d.py``, where the bug survives.
Between the reviewed heads git reports ``R067 b.py -> c.py``: before #3466 X
followed that pairing onto the clean ``c.py``, nothing reported it there, and
its thread was resolved while the provider reported the bug on ``d.py``.

A rename git scores below ``WEAK_RENAME_SIMILARITY`` now moves a finding only
when the round reports it at the destination. A finding nothing reports there
stays where it was stored, open, unverified and never resolved, even when the
low-similarity rename was a real one and the bug was fixed on the way. That
case is held, not resolved: holding can leave a duplicate thread, but a wrong
resolve closes a live one. A rename git scores at or above the threshold
carries and resolves findings as before.

Every round runs through ``run_review`` on a scratch git repository, in delta
and in full mode, with the harness of #3413.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.git_line_map import WEAK_RENAME_SIMILARITY
from lintro.ai.review.models.delta_plan import LOCATION_WEAK_RENAME
from lintro.ai.review.models.review_state import ReviewState
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import commit, git, lines

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]


def _carried_to_b(
    tmp_path: Path,
    *,
    force_full: bool,
) -> tuple[Path, str, ReviewState, str, str]:
    """Review X on ``a.py``, then carry it across ``a.py -> b.py``.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for the carrying round.

    Returns:
        The repository, the base commit, the state after round two, X's key
        and the head round two reviewed.
    """
    repo, base, prior = harness._first_round(tmp_path)
    key = prior.findings[0].key
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename a to b")
    second = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    harness._assert_carried_with_thread(round_=second, key=key, file="b.py")
    return repo, base, second.state, key, git(repo, "rev-parse", "HEAD")


def _b_with_tail(*, keep: int, tail: str) -> str:
    """Return ``b.py``'s text with every line after ``keep`` replaced.

    Args:
        keep: Leading lines of ``b.py`` kept; X sits on line 81.
        tail: Label of the replacement lines.

    Returns:
        300 lines, the first ``keep`` of them ``b.py``'s.
    """
    head = lines(300, label="case").splitlines(keepends=True)[:keep]
    rest = lines(300, label=tail).splitlines(keepends=True)[keep:]
    return "".join(head + rest)


def _rename_score(*, repo: Path, since: str, source: str) -> int:
    """Return git's similarity score for ``source``'s rename since a head.

    Uses the flags the review lists renames with.

    Args:
        repo: The repository.
        since: The earlier reviewed head.
        source: The renamed path at ``since``.

    Returns:
        The score in percent.
    """
    out = git(
        repo,
        "diff",
        "--name-status",
        "--find-renames",
        "--break-rewrites",
        f"{since}..HEAD",
    )
    (status,) = (
        row.split("\t")[0]
        for row in out.splitlines()
        if row.startswith("R") and row.split("\t")[1] == source
    )
    return int(status[1:])


def _assert_held_on_b(*, round_: harness._Round, key: str) -> None:
    """X is carried unverified on ``b.py`` with its thread open.

    Args:
        round_: The round.
        key: X's key.
    """
    assert_that(round_.resolved_threads).is_empty()
    assert_that(round_.match.resolved).is_empty()
    stored = next(record for record in round_.state.findings if record.key == key)
    assert_that(
        (stored.status, stored.file, stored.inline_comment_id, stored.unverified),
    ).is_equal_to((FindingStatus.OPEN, "b.py", harness._THREAD_ID, True))
    held = {entry[0]: entry for entry in round_.result.metadata.relocated_findings}
    assert_that(held[key][3]).is_equal_to(LOCATION_WEAK_RENAME)
    (row,) = (
        line for line in round_.sticky.splitlines() if f"lintro-finding:{key}" in line
    )
    assert_that(row).does_not_contain("~~")


@pytest.mark.parametrize("force_full", _MODES)
def test_a_look_alike_paired_by_similarity_does_not_resolve_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``R067 b.py -> c.py`` to a clean look-alike, X live on ``d.py``: held."""
    repo, base, prior, key, reviewed = _carried_to_b(tmp_path, force_full=force_full)
    (repo / "a.py").write_text(_b_with_tail(keep=212, tail="other"))
    commit(repo, "unrelated a.py that resembles b.py")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    (repo / "d.py").write_text(lines(300, label="rewritten"))
    commit(repo, "move the look-alike to c.py and rewrite b.py into d.py")
    assert_that(_rename_score(repo=repo, since=reviewed, source="b.py")).is_less_than(
        WEAK_RENAME_SIMILARITY,
    )

    third = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="d.py")],
        force_full=force_full,
    )

    # The live bug on d.py opens a second record; X's thread is not resolved.
    assert_that(third.counts).is_equal_to((1, 1, 0))
    (new,) = third.match.new
    assert_that(new.file).is_equal_to("d.py")
    _assert_held_on_b(round_=third, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_low_similarity_rename_whose_bug_was_fixed_is_held(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A real ``b.py -> c.py`` rename under the threshold, X fixed: held open."""
    repo, base, prior, key, reviewed = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "mv", "b.py", "c.py")
    # Lines 61-150 rewritten, X's line 81 among them: about 70% alike.
    kept = lines(300, label="case").splitlines(keepends=True)
    fixed = lines(300, label="fixed").splitlines(keepends=True)
    (repo / "c.py").write_text("".join(kept[:60] + fixed[60:150] + kept[150:]))
    commit(repo, "rename b to c, rewriting X's region")
    assert_that(_rename_score(repo=repo, since=reviewed, source="b.py")).is_less_than(
        WEAK_RENAME_SIMILARITY,
    )

    third = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
    )

    assert_that(third.counts).is_equal_to((1, 0, 0))
    _assert_held_on_b(round_=third, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_low_similarity_rename_carries_a_finding_reported_there(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X reported on the weak rename's destination keeps its key and thread."""
    repo, base, prior, key, reviewed = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "mv", "b.py", "c.py")
    (repo / "c.py").write_text(_b_with_tail(keep=210, tail="edited"))
    commit(repo, "rename b to c with heavy edits below X")
    assert_that(_rename_score(repo=repo, since=reviewed, source="b.py")).is_less_than(
        WEAK_RENAME_SIMILARITY,
    )

    third = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    harness._assert_carried_with_thread(round_=third, key=key, file="c.py")


@pytest.mark.parametrize("force_full", _MODES)
def test_a_high_similarity_rename_still_carries_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A ``b.py -> c.py`` rename scored at 95% or more carries X as before."""
    repo, base, prior, key, reviewed = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "mv", "b.py", "c.py")
    (repo / "c.py").write_text(_b_with_tail(keep=290, tail="touched"))
    commit(repo, "rename b to c with a small edit")
    assert_that(
        _rename_score(repo=repo, since=reviewed, source="b.py"),
    ).is_greater_than_or_equal_to(95)

    third = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    harness._assert_carried_with_thread(round_=third, key=key, file="c.py")
    held = {entry[0]: entry[3] for entry in third.result.metadata.relocated_findings}
    assert_that(held.get(key)).is_not_equal_to(LOCATION_WEAK_RENAME)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_high_similarity_rename_whose_bug_was_fixed_resolves(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X fixed across a 95%+ ``b.py -> c.py`` rename resolves, as before."""
    repo, base, prior, key, reviewed = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "mv", "b.py", "c.py")
    text = lines(300, label="case").replace("case_81 = 81\n", "fixed_81 = 81\n")
    (repo / "c.py").write_text(text)
    commit(repo, "rename b to c and fix X")
    assert_that(
        _rename_score(repo=repo, since=reviewed, source="b.py"),
    ).is_greater_than_or_equal_to(95)

    third = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
    )

    assert_that(third.counts).is_equal_to((0, 0, 1))
    (resolved,) = third.match.resolved
    assert_that((resolved.key, resolved.file)).is_equal_to((key, "c.py"))
    assert_that(third.resolved_threads).is_length(1)
