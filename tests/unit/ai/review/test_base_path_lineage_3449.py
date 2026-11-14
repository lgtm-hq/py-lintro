"""A finding first seen on an already-renamed file keeps its base path (#3449).

The pull request renames ``a.py -> b.py`` and round one reports X on ``b.py``.
The record never moved, so before #3449 it kept no first path
(``FindingRecord.origin_path``). Round two renames ``b.py -> c.py`` with heavy
edits: git reads a delete plus an add, the pull request's metadata names only
``a.py -> c.py``, and X was reported twice, carried on ``b.py`` with its thread
open and new on ``c.py``. A new record now takes the path its file had at the
pull request's base, so the lineage fallback of #3432 applies, with every one
of its guards: where ``a.py`` was reused, the destination is contested, or the
range's history is cut, X is held and no thread is resolved.

Every round runs through ``run_review`` on a scratch git repository, in delta
and in full mode, with the harness of #3413.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.incremental_coverage import (
    CheckpointStamp,
    write_incremental_coverage_part,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.state_store import load_ci_state
from tests.unit.ai.review import test_chained_rename_lineage_3432 as lineage
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import commit, git, lines

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]


def _text(*parts: tuple[str, int, int]) -> str:
    """Join numbered filler lines from several labels.

    Args:
        *parts: ``(label, first, last)`` runs of :func:`lines`, 1-based and
            inclusive.

    Returns:
        The text, newline-terminated.
    """
    return "".join(
        "".join(lines(last, label=label).splitlines(keepends=True)[first - 1 :])
        for label, first, last in parts
    )


def _with_bug(text: str) -> str:
    """Put X's line, line 81, into a text so the diff shows it.

    Args:
        text: The file's text.

    Returns:
        The text with line 81 replaced.
    """
    numbered = text.splitlines(keepends=True)
    numbered[80] = "bug_at_81 = True\n"
    return "".join(numbered)


#: ``b.py``: two thirds of ``a.py``, so the pull request reads ``a.py -> b.py``.
_B_TEXT = _text(("case", 1, 100), ("base", 101, 300))
#: ``c.py``: over half of ``a.py`` but under a quarter of ``b.py``, so the
#: range since ``b.py`` reads as a delete plus an add while the pull request
#: reads ``a.py -> c.py``.
_C_TEXT = _with_bug(_text(("base", 1, 170), ("heavy", 171, 300)))


def _reported_on_b(
    tmp_path: Path,
    *,
    force_full: bool,
) -> tuple[Path, str, ReviewState, str]:
    """Rename ``a.py -> b.py``, then report X on ``b.py`` for the first time.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for the round.

    Returns:
        The repository, the base commit, the state after round one with X's
        thread, and X's key.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    base = commit(repo, "base")
    git(repo, "mv", "a.py", "b.py")
    (repo / "b.py").write_text(_B_TEXT)
    commit(repo, "rename a.py to b.py")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).matches(r"R\d+\ta\.py\tb\.py")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    state = harness._with_thread(first.state)
    (record,) = state.findings
    assert_that((record.file, record.origin_path)).is_equal_to(("b.py", "a.py"))
    return repo, base, state, record.key


def _rename_b_to_c(repo: Path) -> None:
    """Rename ``b.py -> c.py`` with edits git cannot pair.

    Args:
        repo: The repository.
    """
    git(repo, "mv", "b.py", "c.py")
    (repo / "c.py").write_text(_C_TEXT)
    commit(repo, "rename b.py to c.py, heavily edited")


@pytest.mark.parametrize("force_full", _MODES)
def test_a_finding_first_seen_on_a_renamed_file_follows_its_next_rename(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X first reported on ``b.py`` carries to ``c.py`` with its thread."""
    repo, base, state, key = _reported_on_b(tmp_path, force_full=force_full)
    reviewed_head = git(repo, "rev-parse", "HEAD")
    _rename_b_to_c(repo)
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).matches(r"R\d+\ta\.py\tc\.py")
    assert_that(
        git(repo, "diff", "-M", "-B", "--name-status", f"{reviewed_head}..HEAD"),
    ).contains("D\tb.py", "A\tc.py")

    second = harness._round(
        repo=repo,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    harness._assert_carried_with_thread(round_=second, key=key, file="c.py")
    (carried,) = second.state.findings
    assert_that(carried.origin_path).is_equal_to("a.py")


@pytest.mark.parametrize("force_full", _MODES)
def test_a_reused_base_path_holds_a_finding_first_seen_after_the_rename(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """A new ``a.py`` renamed to ``c.py`` is not X's file: X stays on ``b.py``."""
    repo, base, state, key = _reported_on_b(tmp_path, force_full=force_full)
    reviewed_head = git(repo, "rev-parse", "HEAD")
    # Like the base's a.py, unlike b.py: git cannot pair b.py with it.
    (repo / "a.py").write_text(_C_TEXT)
    commit(repo, "re-create an unrelated a.py")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    (repo / "d.py").write_text(lines(300, label="rewritten"))
    commit(repo, "rename the new a.py, rename and rewrite b.py")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).matches(r"R\d+\ta\.py\tc\.py")
    assert_that(
        git(repo, "diff", "-M", "-B", "--name-status", f"{reviewed_head}..HEAD"),
    ).contains("D\tb.py", "A\tc.py")

    second = harness._round(
        repo=repo,
        base=base,
        prior=state,
        reported=[harness._x(file="d.py")],
        force_full=force_full,
    )

    lineage._assert_held_on_b(round_=second, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_destination_git_pairs_elsewhere_holds_a_finding_first_seen_after_the_rename(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X on ``b.py`` (from ``a.py``) and on ``e.py`` (from ``d.py``); ``e.py`` becomes ``c.py``.

    Git pairs ``e.py -> c.py`` while the pull request names ``a.py -> c.py``.
    Both records reaching ``c.py`` would let the pairing hand ``e.py``'s
    sighting to ``b.py``'s thread and resolve the live one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    (repo / "d.py").write_text(lines(300, label="dbase"))
    base = commit(repo, "two originals")
    git(repo, "mv", "a.py", "b.py")
    git(repo, "mv", "d.py", "e.py")
    (repo / "b.py").write_text(_B_TEXT)
    (repo / "e.py").write_text(_with_bug(lines(300, label="dbase")))
    commit(repo, "rename both")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="b.py"), harness._x(file="e.py")],
        force_full=force_full,
    )
    assert_that(
        {record.file: record.origin_path for record in first.state.findings},
    ).is_equal_to({"b.py": "a.py", "e.py": "d.py"})
    threaded = replace(
        first.state,
        findings=tuple(
            replace(record, inline_comment_id=3449 + index)
            for index, record in enumerate(
                sorted(first.state.findings, key=lambda item: item.file),
            )
        ),
    )
    git(repo, "rm", "-q", "b.py")
    git(repo, "mv", "e.py", "c.py")
    commit(repo, "remove b.py, rename e.py to c.py")
    claimed = [
        ChangedFile(
            path="c.py",
            status=ChangedFileStatus.RENAMED,
            additions=300,
            deletions=300,
            previous_path="a.py",
        ),
        ChangedFile(
            path="d.py",
            status=ChangedFileStatus.DELETED,
            additions=0,
            deletions=300,
        ),
    ]

    with lineage._with_pr_files(repo=repo, base=base, changed_files=claimed):
        second = harness._round(
            repo=repo,
            base=base,
            prior=threaded,
            reported=[harness._x(file="c.py")],
            force_full=force_full,
        )

    assert_that(second.counts).is_equal_to((2, 0, 0))
    assert_that(second.resolved_threads).is_empty()
    assert_that(
        {
            record.inline_comment_id: (record.file, record.status)
            for record in second.state.findings
        },
    ).is_equal_to(
        {3449: ("b.py", FindingStatus.OPEN), 3450: ("c.py", FindingStatus.OPEN)},
    )


@pytest.mark.parametrize("force_full", _MODES)
def test_a_shallow_cut_range_holds_a_finding_first_seen_after_the_rename(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """The commits between the two reviewed heads are not fetched: X is held.

    Without them nothing proves ``a.py`` was never re-created in the range.
    """
    (tmp_path / "source").mkdir()
    repo, base, state, key = _reported_on_b(
        tmp_path / "source",
        force_full=force_full,
    )
    reviewed_head = git(repo, "rev-parse", "HEAD")
    (repo / "unrelated.py").write_text("unrelated = True\n")
    commit(repo, "an unrelated commit inside the range")
    _rename_b_to_c(repo)
    clone = lineage._shallow_clone(
        tmp_path=tmp_path,
        repo=repo,
        base=base,
        previous=reviewed_head,
    )
    assert_that(
        git(clone, "log", "--format=%s", f"{reviewed_head}..HEAD"),
    ).does_not_contain("unrelated commit")

    second = harness._round(
        repo=clone,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        force_full=force_full,
    )

    lineage._assert_held_on_b(round_=second, key=key)


@pytest.mark.parametrize("force_full", _MODES)
def test_a_finding_on_a_file_the_pr_did_not_rename_has_no_base_path(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X on an edited ``a.py`` and on an added ``n.py`` while ``x.py -> y.py``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    (repo / "x.py").write_text(lines(300, label="xbase"))
    base = commit(repo, "base")
    (repo / "a.py").write_text(_B_TEXT)
    (repo / "n.py").write_text(lines(300, label="added"))
    git(repo, "mv", "x.py", "y.py")
    commit(repo, "edit a.py, add n.py, rename x.py")
    assert_that(
        git(repo, "diff", "-M", "--name-status", f"{base}..HEAD"),
    ).contains("R100\tx.py\ty.py")

    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py"), harness._x(file="n.py")],
        force_full=force_full,
    )

    assert_that(
        {record.file: record.origin_path for record in first.state.findings},
    ).is_equal_to({"a.py": "", "n.py": ""})
    assert_that(
        ["origin_path" in record.to_dict() for record in first.state.findings],
    ).is_equal_to([False, False])


def test_a_checkpoint_records_the_base_path_a_resumed_round_inherits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SIGTERM checkpoint stores X on ``b.py`` with ``a.py``; ``n.py`` gets none."""
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="b.py",
                status=ChangedFileStatus.RENAMED,
                additions=1,
                deletions=0,
                previous_path="a.py",
            ),
            ChangedFile(path="n.py", status="added", additions=1, deletions=0),
        ],
        unified_diff="".join(
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            "@@ -1 +1,2 @@\n x\n+y\n"
            for path in ("b.py", "n.py")
        ),
    )
    findings = tuple(
        ReviewFinding(
            severity=Severity.P2,
            category="logic-bug",
            file=path,
            line=2,
            title="X",
            description="Reproducible bug",
            cause="bad logic",
            fix="repair the logic",
            confidence="high",
        )
        for path in ("b.py", "n.py")
    )

    write_incremental_coverage_part(
        collected=[
            ChunkReviewPartial(
                findings=findings,
                input_tokens=0,
                output_tokens=0,
                cost_estimate=0.0,
                files=("b.py", "n.py"),
            ),
        ],
        resume=plan_resume(context=context, prior=None),
        context=context,
        prior_state=None,
        force_full=False,
        sequence=1,
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
        stamp=CheckpointStamp(pr_number=3449),
    )

    loaded = load_ci_state(directory=tmp_path, repo="lgtm-hq/py-lintro", pr_number=3449)
    assert_that(
        {record.file: record.origin_path for record in loaded.findings},
    ).is_equal_to({"b.py": "a.py", "n.py": ""})
