"""Tests for git-checkpoint capture, restore, diff, and pruning."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess drives git in temp test repos; shell=False
from pathlib import Path

from assertpy import assert_that

from lintro.ai.checkpoints import (
    CHECKPOINT_REF_PREFIX,
    capture_checkpoint,
    diff_checkpoint,
    git_checkpoints_available,
    list_checkpoint_refs,
    prune_checkpoints,
    restore_checkpoint,
)
from lintro.ai.models import AIFixSuggestion
from lintro.ai.undo import prepare_fix_batch, restore_undo


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
    )


def _init_git_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with an initial commit."""
    _run(["git", "init"], cwd=tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path)
    _run(["git", "config", "user.name", "Test User"], cwd=tmp_path)
    # Avoid depending on system default branch name.
    _run(["git", "checkout", "-b", "main"], cwd=tmp_path)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("alpha = 1\n", encoding="utf-8")
    other = tmp_path / "other.py"
    other.write_text("keep = True\n", encoding="utf-8")
    _run(["git", "add", "tracked.py", "other.py"], cwd=tmp_path)
    _run(["git", "commit", "-m", "init"], cwd=tmp_path)
    return tmp_path


def _index_sha(*, cwd: Path) -> str:
    """Return the sha of the real index tree (user index must stay stable)."""
    env = os.environ.copy()
    # Force using the real index (clear any leaked GIT_INDEX_FILE).
    env.pop("GIT_INDEX_FILE", None)
    result = (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            ["git", "write-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    )
    return result.stdout.strip()


def _staged_diff(*, cwd: Path) -> str:
    result = (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            ["git", "diff", "--cached"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
    )
    return result.stdout


def _make_suggestion(file: str, original: str, suggested: str) -> AIFixSuggestion:
    return AIFixSuggestion(
        file=file,
        line=1,
        code="E001",
        tool_name="ruff",
        original_code=original,
        suggested_code=suggested,
        diff="",
        explanation="fix",
    )


def test_git_checkpoints_available_true_in_repo(tmp_path: Path) -> None:
    """Usable non-bare work trees report checkpoints available."""
    repo = _init_git_repo(tmp_path)
    assert_that(git_checkpoints_available(repo)).is_true()


def test_git_checkpoints_available_false_outside_git(tmp_path: Path) -> None:
    """Non-git directories cannot use git checkpoints."""
    assert_that(git_checkpoints_available(tmp_path)).is_false()


def test_capture_restore_round_trip_multi_file(tmp_path: Path) -> None:
    """Capture then restore returns multiple mutated files to prior content."""
    repo = _init_git_repo(tmp_path)
    a = repo / "tracked.py"
    b = repo / "other.py"
    untracked = repo / "new_file.py"
    untracked.write_text("untracked = 0\n", encoding="utf-8")

    checkpoint = capture_checkpoint(
        ["tracked.py", "other.py", "new_file.py"],
        workspace_root=repo,
        keep=10,
    )
    assert_that(checkpoint).is_not_none()
    assert_that(checkpoint.ref).starts_with(CHECKPOINT_REF_PREFIX)  # type: ignore[union-attr]

    a.write_text("alpha = 99\n", encoding="utf-8")
    b.write_text("keep = False\n", encoding="utf-8")
    untracked.write_text("untracked = 1\n", encoding="utf-8")

    restore_checkpoint(checkpoint)  # type: ignore[arg-type]
    assert_that(a.read_text(encoding="utf-8")).is_equal_to("alpha = 1\n")
    assert_that(b.read_text(encoding="utf-8")).is_equal_to("keep = True\n")
    assert_that(untracked.read_text(encoding="utf-8")).is_equal_to("untracked = 0\n")


def test_capture_does_not_touch_dirty_user_index(tmp_path: Path) -> None:
    """Capture must leave staged/unstaged user index state untouched."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    tracked.write_text("alpha = 2\n", encoding="utf-8")
    _run(["git", "add", "tracked.py"], cwd=repo)
    staged_before = _staged_diff(cwd=repo)
    index_before = _index_sha(cwd=repo)
    head_before = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    # Also leave an unstaged edit on another file.
    other = repo / "other.py"
    other.write_text("keep = 'dirty'\n", encoding="utf-8")

    checkpoint = capture_checkpoint(
        ["tracked.py", "other.py"],
        workspace_root=repo,
        keep=10,
    )
    assert_that(checkpoint).is_not_none()

    assert_that(_staged_diff(cwd=repo)).is_equal_to(staged_before)
    assert_that(_index_sha(cwd=repo)).is_equal_to(index_before)
    assert_that(
        _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip(),
    ).is_equal_to(
        head_before,
    )
    # Working tree dirty content for non-capture side effects must remain.
    assert_that(other.read_text(encoding="utf-8")).is_equal_to("keep = 'dirty'\n")


def test_restore_does_not_touch_dirty_user_index(tmp_path: Path) -> None:
    """Restore must not alter the user's staged index."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    tracked.write_text("alpha = 2\n", encoding="utf-8")
    _run(["git", "add", "tracked.py"], cwd=repo)
    staged_before = _staged_diff(cwd=repo)
    index_before = _index_sha(cwd=repo)

    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    tracked.write_text("alpha = 3\n", encoding="utf-8")
    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(tracked.read_text(encoding="utf-8")).is_equal_to("alpha = 2\n")
    assert_that(_staged_diff(cwd=repo)).is_equal_to(staged_before)
    assert_that(_index_sha(cwd=repo)).is_equal_to(index_before)


def test_untracked_target_included_in_checkpoint(tmp_path: Path) -> None:
    """Untracked target files are snapshotted and restorable."""
    repo = _init_git_repo(tmp_path)
    newbie = repo / "scratch.py"
    newbie.write_text("scratch = 1\n", encoding="utf-8")

    checkpoint = capture_checkpoint(["scratch.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    newbie.write_text("scratch = 9\n", encoding="utf-8")
    restore_checkpoint(checkpoint)  # type: ignore[arg-type]
    assert_that(newbie.read_text(encoding="utf-8")).is_equal_to("scratch = 1\n")


def test_user_edited_between_capture_and_rollback(tmp_path: Path) -> None:
    """Rollback restores lintro targets even if the user edited them mid-run.

    Semantics: checkpoint rollback always returns targeted paths to the
    pre-batch snapshot, overwriting intervening user edits on those paths.
    """
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()

    # Simulate lintro mutation, then a user edit on the same file.
    tracked.write_text("alpha = 'lintro'\n", encoding="utf-8")
    tracked.write_text("alpha = 'user-edit'\n", encoding="utf-8")

    restore_checkpoint(checkpoint)  # type: ignore[arg-type]
    assert_that(tracked.read_text(encoding="utf-8")).is_equal_to("alpha = 1\n")


def test_diff_reports_lintro_changes(tmp_path: Path) -> None:
    """diff_checkpoint reflects working-tree changes since capture."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    tracked.write_text("alpha = 42\n", encoding="utf-8")

    diff = diff_checkpoint(checkpoint)  # type: ignore[arg-type]
    assert_that(diff).contains("alpha = 1")
    assert_that(diff).contains("alpha = 42")


def test_non_git_falls_back_to_file_undo(tmp_path: Path) -> None:
    """Outside git, prepare_fix_batch uses file-snapshot fallback."""
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    suggestion = _make_suggestion(str(target), "x = 1\n", "x = 2\n")

    state = prepare_fix_batch([suggestion], tmp_path, retention=10)
    assert_that(state).is_not_none()
    assert_that(state.kind).is_equal_to("file")  # type: ignore[union-attr]
    assert_that(state.checkpoint).is_none()  # type: ignore[union-attr]

    target.write_text("x = 2\n", encoding="utf-8")
    restore_undo(state)  # type: ignore[arg-type]
    assert_that(target.read_text(encoding="utf-8")).is_equal_to("x = 1\n")


def test_prune_keeps_last_n_refs(tmp_path: Path) -> None:
    """prune_checkpoints deletes older refs beyond the retention limit."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"

    refs: list[str] = []
    for i in range(5):
        tracked.write_text(f"alpha = {i}\n", encoding="utf-8")
        cp = capture_checkpoint(
            ["tracked.py"],
            workspace_root=repo,
            run_id=f"100{i}-abcd{i:04d}",
            keep=100,  # disable prune during capture
        )
        assert_that(cp).is_not_none()
        refs.append(cp.ref)  # type: ignore[union-attr]

    listed = list_checkpoint_refs(workspace_root=repo)
    assert_that(listed).is_length(5)

    deleted = prune_checkpoints(workspace_root=repo, keep=2)
    assert_that(deleted).is_equal_to(3)
    remaining = list_checkpoint_refs(workspace_root=repo)
    assert_that(remaining).is_length(2)
    assert_that(remaining).is_equal_to(refs[-2:])


def test_prepare_fix_batch_uses_git_in_repo(tmp_path: Path) -> None:
    """prepare_fix_batch prefers git checkpoints inside a repository."""
    repo = _init_git_repo(tmp_path)
    suggestion = _make_suggestion("tracked.py", "alpha = 1\n", "alpha = 2\n")
    state = prepare_fix_batch([suggestion], repo, retention=10)
    assert_that(state).is_not_none()
    assert_that(state.kind).is_equal_to("git")  # type: ignore[union-attr]
    assert_that(state.checkpoint).is_not_none()  # type: ignore[union-attr]

    (repo / "tracked.py").write_text("alpha = 2\n", encoding="utf-8")
    restore_undo(state, ["tracked.py"])  # type: ignore[arg-type]
    assert_that((repo / "tracked.py").read_text(encoding="utf-8")).is_equal_to(
        "alpha = 1\n",
    )
