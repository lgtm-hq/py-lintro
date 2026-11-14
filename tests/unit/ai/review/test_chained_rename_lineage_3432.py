"""A finding follows a second rename git reads as a delete plus an add (#3432).

Round one reports X on ``a.py``. Round two carries it across ``a.py -> b.py``.
Round three renames ``b.py -> c.py`` with heavy edits, so git sees a delete
plus an add, while the pull request's rename metadata only names
``a.py -> c.py``. X was stored on ``b.py``, so the metadata did not apply to it
and the round reported X twice: once new on ``c.py``, once carried on
``b.py`` with its thread left open. The record now keeps the path it was first
tracked at (``FindingRecord.origin_path``), and that path is looked up too,
except where it could name a different file or another record already arrives
at the destination: there the round holds X as before, which can duplicate a
thread but never resolves a live one.

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
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.enums.finding_status import FindingStatus
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


@pytest.mark.parametrize("force_full", _MODES)
def test_a_reused_first_path_renamed_elsewhere_does_not_take_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A new ``a.py`` renamed to ``c.py`` is not X's file; X is held on ``b.py``."""
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    (repo / "a.py").write_text(lines(300, label="base"))
    commit(repo, "re-create an unrelated a.py")
    middle = harness._round(
        repo=repo,
        base=base,
        prior=state,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    text = lines(300, label="rewritten").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "d.py").write_text("".join(text))
    commit(repo, "rename the new a.py, rename and rewrite b.py")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).contains("R100\ta.py\tc.py")

    third = harness._round(
        repo=repo,
        base=base,
        prior=middle.state,
        reported=[harness._x(file="d.py")],
        force_full=force_full,
    )

    assert_that(third.counts).is_equal_to((1, 1, 0))
    assert_that(third.resolved_threads).is_empty()
    (carried,) = third.match.carried
    assert_that((carried.key, carried.file)).is_equal_to((key, "b.py"))


@pytest.mark.parametrize("force_full", _MODES)
def test_two_findings_converging_on_one_file_keep_their_threads(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X on ``b.py`` (from ``a.py``) and on ``e.py`` (from ``d.py``); ``e.py`` becomes ``c.py``.

    Git pairs ``e.py -> c.py`` while the PR pairs ``a.py -> c.py``. Moving
    both records to ``c.py`` let the pairing hand ``e.py``'s sighting to
    ``b.py``'s thread and resolve the live one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    (repo / "d.py").write_text(lines(300, label="otherbase"))
    base = commit(repo, "two originals")
    (repo / "a.py").write_text(lines(300, label="case"))
    text = lines(300, label="base").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "d.py").write_text("".join(text))
    commit(repo, "the same finding on two files")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py"), harness._x(file="d.py")],
        force_full=force_full,
    )
    threaded = replace(
        first.state,
        findings=tuple(
            replace(record, inline_comment_id=3413 + index)
            for index, record in enumerate(first.state.findings)
        ),
    )
    git(repo, "mv", "a.py", "b.py")
    git(repo, "mv", "d.py", "e.py")
    commit(repo, "rename both")
    second = harness._round(
        repo=repo,
        base=base,
        prior=threaded,
        reported=[harness._x(file="b.py"), harness._x(file="e.py")],
        force_full=force_full,
    )
    assert_that(second.counts).is_equal_to((2, 0, 0))
    git(repo, "rm", "-q", "b.py")
    git(repo, "mv", "e.py", "c.py")
    commit(repo, "remove b.py, rename e.py to c.py")

    third = harness._round(
        repo=repo,
        base=base,
        prior=second.state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    assert_that(third.counts).is_equal_to((2, 0, 0))
    assert_that(third.resolved_threads).is_empty()
    by_thread = {record.inline_comment_id: record for record in third.state.findings}
    assert_that(
        {thread: (record.file, record.status) for thread, record in by_thread.items()},
    ).is_equal_to(
        {3413: ("b.py", FindingStatus.OPEN), 3414: ("c.py", FindingStatus.OPEN)},
    )


def _shallow_clone(
    *,
    tmp_path: Path,
    repo: Path,
    base: str,
    previous: str = "",
    whole_range: bool = False,
) -> Path:
    """Check the PR head out as the workflow does: a depth-1 trusted branch.

    Args:
        tmp_path: Pytest temp dir; the clone goes in ``workflow``.
        repo: The source repository at the PR head.
        base: The base commit, the trusted branch's tip.
        previous: A previously reviewed head to fetch at depth 1 as well,
            so both ends of its range are readable but not what lies
            between them.
        whole_range: Fetch the head's whole history down to the base
            instead of the head alone.

    Returns:
        The shallow clone, checked out at the PR head.
    """
    git(repo, "update-ref", "refs/heads/trusted", base)
    git(repo, "update-ref", "refs/pull/1/head", git(repo, "rev-parse", "HEAD"))
    refspecs = ["+refs/pull/1/head:refs/pr"]
    if previous:
        git(repo, "update-ref", "refs/heads/previous", previous)
        refspecs.append("+refs/heads/previous:refs/previous")
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
    git(
        clone,
        "fetch",
        "-q",
        *([] if whole_range else ["--depth", "1"]),
        "--no-tags",
        "origin",
        *refspecs,
    )
    git(clone, "checkout", "-q", "--detach", "refs/pr")
    assert_that(
        git(clone, "rev-parse", "--is-shallow-repository"),
    ).is_equal_to("true")
    return clone


def _assert_held_on_b(*, round_: harness._Round, key: str) -> None:
    """X stays open on ``b.py`` with its thread and is reported again elsewhere.

    Args:
        round_: The round.
        key: X's original key.
    """
    assert_that(round_.counts).is_equal_to((1, 1, 0))
    assert_that(round_.resolved_threads).is_empty()
    (carried,) = round_.match.carried
    assert_that((carried.key, carried.file, carried.inline_comment_id)).is_equal_to(
        (key, "b.py", harness._THREAD_ID),
    )


@pytest.mark.parametrize(
    ("force_full", "reason"),
    [
        pytest.param(False, DeltaReason.NOT_ANCESTOR, id="automatic"),
        pytest.param(True, DeltaReason.EXPLICIT_FULL, id="forced-full"),
    ],
)
def test_a_rewritten_shallow_history_holds_the_finding(
    tmp_path: Path,
    force_full: bool,
    reason: DeltaReason,
) -> None:
    """X's head is gone from the clone, so ``a.py``'s provenance is unknown: X is held."""
    (tmp_path / "source").mkdir()
    repo, base, state, key = _carried_to_b(tmp_path / "source", force_full=False)
    git(repo, "reset", "-q", "--hard", base)
    git(repo, "mv", "a.py", "c.py")
    text = lines(300, label="base").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "c.py").write_text("".join(text))
    commit(repo, "rewritten feature, renamed to c.py")
    clone = _shallow_clone(tmp_path=tmp_path, repo=repo, base=base)

    third = harness._round(
        repo=clone,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    assert_that(third.result.metadata.delta_reason).is_equal_to(reason)
    _assert_held_on_b(round_=third, key=key)


@pytest.mark.parametrize("force_full", _MODES)
@pytest.mark.parametrize("shallow", [False, True], ids=["readable", "unreachable"])
def test_a_first_path_reused_between_reviews_does_not_take_the_finding(
    tmp_path: Path,
    force_full: bool,
    shallow: bool,
) -> None:
    """A new ``a.py`` is created and renamed to ``c.py`` with no review in between.

    The range's endpoints show only ``b.py`` deleted and ``c.py``/``d.py``
    added, so only the commits in between show ``a.py`` was another file.
    """
    (tmp_path / "source").mkdir()
    repo, base, state, key = _carried_to_b(tmp_path / "source", force_full=force_full)
    reviewed_head = git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text(lines(300, label="base"))
    commit(repo, "re-create an unrelated a.py")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    text = lines(300, label="rewritten").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "d.py").write_text("".join(text))
    commit(repo, "rename the new a.py, rename and rewrite b.py")
    endpoints = git(repo, "diff", "-M", "-B", "--name-status", f"{reviewed_head}..HEAD")
    assert_that(endpoints).contains("D\tb.py").does_not_contain("a.py")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).contains("R100\ta.py\tc.py")
    target = (
        _shallow_clone(tmp_path=tmp_path, repo=repo, base=base) if shallow else repo
    )

    third = harness._round(
        repo=target,
        base=base,
        prior=state,
        reported=[harness._x(file="d.py")],
        force_full=force_full,
    )

    _assert_held_on_b(round_=third, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_reuse_hidden_by_the_shallow_boundary_holds_the_finding(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """Both reviewed heads are fetched at depth 1; the commit re-creating ``a.py`` is not.

    ``git log`` over the range succeeds without that commit, so its silence
    is no proof ``a.py`` was never re-created: X is held on ``b.py``.
    """
    (tmp_path / "source").mkdir()
    repo, base, state, key = _carried_to_b(tmp_path / "source", force_full=force_full)
    reviewed_head = git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text(lines(300, label="base"))
    commit(repo, "re-create an unrelated a.py")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    text = lines(300, label="rewritten").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "d.py").write_text("".join(text))
    commit(repo, "rename the new a.py, rename and rewrite b.py")
    clone = _shallow_clone(
        tmp_path=tmp_path,
        repo=repo,
        base=base,
        previous=reviewed_head,
    )
    # Both ends of the range are readable; the re-creating commit is missing.
    git(clone, "cat-file", "-e", reviewed_head)
    assert_that(
        git(clone, "log", "--format=%s", f"{reviewed_head}..HEAD"),
    ).does_not_contain("re-create")

    third = harness._round(
        repo=clone,
        base=base,
        prior=state,
        reported=[harness._x(file="d.py")],
        force_full=force_full,
    )

    _assert_held_on_b(round_=third, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_shallow_clone_holding_the_whole_range_follows_the_first_path(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A clone shallow only below the base still proves ``a.py`` was never re-created."""
    (tmp_path / "source").mkdir()
    repo, base, state, key = _carried_to_b(tmp_path / "source", force_full=force_full)
    git(repo, "mv", "b.py", "c.py")
    text = lines(300, label="base").splitlines(keepends=True)
    text[80] = "bug_at_81 = True\n"
    (repo / "c.py").write_text("".join(text))
    commit(repo, "second rename plus heavy edit")
    clone = _shallow_clone(tmp_path=tmp_path, repo=repo, base=base, whole_range=True)

    third = harness._round(
        repo=clone,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    harness._assert_carried_with_thread(round_=third, key=key, file="c.py")


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
