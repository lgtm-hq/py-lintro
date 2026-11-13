"""A finding follows a second rename git reads as a delete plus an add (#3432).

Round one reports X on ``a.py``. Round two carries it across ``a.py -> b.py``.
Round three renames ``b.py -> c.py`` with heavy edits, so git sees a delete
plus an add, while the pull request's rename metadata only names
``a.py -> c.py``. X was stored on ``b.py``, so the metadata did not apply to it
and the round reported X twice: once new on ``c.py``, once carried on
``b.py`` with its thread left open. The record now keeps the path it was first
tracked at (``FindingRecord.origin_path``), and that path is looked up too.

Every round runs through ``run_review`` on a scratch git repository, in delta
and in full mode, with the harness of #3413.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.finding_record import FindingRecord
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
) -> tuple[Path, str, ReviewState, str]:
    """Review X on ``a.py``, then carry it across ``a.py -> b.py``.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for the carrying round.

    Returns:
        The repository, the base commit, the state after round two, and X's
        key.
    """
    repo, base, prior = harness._first_round(tmp_path)
    key = prior.findings[0].key
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "first rename")
    second = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    harness._assert_carried_with_thread(round_=second, key=key, file="b.py")
    return repo, base, second.state, key


def _with_pr_files(
    *,
    repo: Path,
    base: str,
    changed_files: list[ChangedFile],
) -> AbstractContextManager[object]:
    """Serve the round's context with the given PR changed-file metadata.

    Args:
        repo: The repository.
        base: The base commit.
        changed_files: The metadata the files API would report.

    Returns:
        A patch of the harness's context builder.
    """
    context = harness._git_context(repo=repo, base=base)
    return patch.object(
        harness,
        "_git_context",
        return_value=replace(context, changed_files=changed_files),
    )


@pytest.mark.parametrize("force_full", _MODES)
def test_a_second_rename_read_as_delete_plus_add_carries_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``b.py -> c.py`` loses git pairing; the PR's ``a.py -> c.py`` holds."""
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    old_head = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "b.py", "c.py")
    text = lines(300, label="base").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "c.py").write_text("".join(text))
    commit(repo, "second rename plus heavy edit")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).contains("R099\ta.py\tc.py")
    assert_that(
        git(repo, "diff", "-M", "-B", "--name-status", f"{old_head}..HEAD"),
    ).contains("D\tb.py", "A\tc.py")

    third = harness._round(
        repo=repo,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    harness._assert_carried_with_thread(round_=third, key=key, file="c.py")
    (carried,) = third.state.findings
    assert_that((carried.file, carried.origin_path)).is_equal_to(("c.py", "a.py"))


@pytest.mark.parametrize("force_full", _MODES)
def test_deleting_the_reviewed_file_still_resolves_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``a.py`` deleted with no rename anywhere: X is fixed and its thread closes."""
    repo, base, prior = harness._first_round(tmp_path)
    git(repo, "rm", "-q", "a.py")
    (repo / "other.py").write_text("other = True\n")
    commit(repo, "delete the reviewed file")

    second = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
    )

    assert_that(second.counts).is_equal_to((0, 0, 1))
    assert_that(second.match.resolved[0].file).is_equal_to("a.py")
    assert_that(second.resolved_threads).is_length(1)


@pytest.mark.parametrize("force_full", _MODES)
def test_deleting_the_renamed_file_does_not_move_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``b.py`` deleted and no ``a.py`` rename left in the PR: X stays on ``b.py``."""
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "rm", "-q", "b.py")
    (repo / "other.py").write_text("other = True\n")
    commit(repo, "delete the renamed file")

    third = harness._round(
        repo=repo,
        base=base,
        prior=state,
        reported=[],
        force_full=force_full,
    )

    assert_that(third.match.new).is_empty()
    stored = next(record for record in third.state.findings if record.key == key)
    assert_that(stored.file).is_equal_to("b.py")
    moved = {
        path for _key, path, _line, _sha in third.result.metadata.relocated_findings
    }
    assert_that(moved).does_not_contain("other.py")


@pytest.mark.parametrize("force_full", _MODES)
def test_recreating_the_renamed_file_is_not_a_rename(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``b.py`` deleted and re-added keeps X there despite ``a.py -> c.py``."""
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "rm", "-q", "b.py")
    commit(repo, "delete b")
    (repo / "b.py").write_text(lines(300, label="replacement"))
    (repo / "c.py").write_text(lines(300, label="unrelated"))
    commit(repo, "recreate b and add c")
    stale = [
        ChangedFile(
            path="b.py",
            status=ChangedFileStatus.ADDED,
            additions=300,
            deletions=0,
        ),
        ChangedFile(
            path="c.py",
            status=ChangedFileStatus.RENAMED,
            additions=300,
            deletions=300,
            previous_path="a.py",
        ),
    ]

    with _with_pr_files(repo=repo, base=base, changed_files=stale):
        third = harness._round(
            repo=repo,
            base=base,
            prior=state,
            reported=[harness._x(file="b.py")],
            force_full=force_full,
        )

    harness._assert_carried_with_thread(round_=third, key=key, file="b.py")


@pytest.mark.parametrize("force_full", _MODES)
def test_an_unrelated_rename_does_not_move_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """``b.py`` deleted while the PR renames ``d.py -> e.py``: X never reaches ``e.py``."""
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    git(repo, "rm", "-q", "b.py")
    (repo / "e.py").write_text(lines(300, label="elsewhere"))
    commit(repo, "delete b and add e")
    context = harness._git_context(repo=repo, base=base)
    unrelated = [
        *(item for item in context.changed_files if item.path != "e.py"),
        ChangedFile(
            path="e.py",
            status=ChangedFileStatus.RENAMED,
            additions=300,
            deletions=300,
            previous_path="d.py",
        ),
    ]

    with _with_pr_files(repo=repo, base=base, changed_files=unrelated):
        third = harness._round(
            repo=repo,
            base=base,
            prior=state,
            reported=[harness._x(file="e.py")],
            force_full=force_full,
        )

    assert_that(third.counts).is_equal_to((1, 1, 0))
    (carried,) = third.match.carried
    assert_that((carried.key, carried.file)).is_equal_to((key, "b.py"))
    assert_that(third.match.new[0].file).is_equal_to("e.py")


def test_a_record_stored_before_origin_paths_loads_without_one() -> None:
    """An older blob has no ``origin_path``; it loads empty and stays absent."""
    payload = {
        "fingerprint": "abc",
        "ordinal": 1,
        "severity": "P2",
        "file": "b.py",
        "line": 81,
        "status": "open",
        "line_sha": "1" * 40,
        "match_fingerprint": "def",
    }

    record = FindingRecord.from_dict(payload)

    assert_that(record).is_not_none()
    assert record is not None
    assert_that(record.origin_path).is_empty()
    assert_that(record.to_dict()).does_not_contain_key("origin_path")


def test_an_origin_path_round_trips_through_the_state_blob() -> None:
    """A moved record keeps its first path across a save and a load."""
    record = FindingRecord(fingerprint="abc", file="c.py", origin_path="a.py")

    restored = FindingRecord.from_dict(record.to_dict())

    assert_that(restored).is_equal_to(record)
