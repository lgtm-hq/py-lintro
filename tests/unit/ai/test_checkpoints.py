"""Tests for git-checkpoint capture, restore, diff, and pruning."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess drives git in temp test repos; shell=False
from pathlib import Path
from unittest.mock import MagicMock

import pytest
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
from lintro.ai.pipeline import _report_checkpoints
from lintro.ai.undo import prepare_fix_batch, restore_undo


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command in a temp repo, isolated from developer git config.

    A global ``commit.gpgsign``, ``core.hooksPath`` or ``init.templateDir``
    would otherwise change how these fixtures behave from machine to machine.

    Args:
        cmd: Full argv.
        cwd: Working directory.

    Returns:
        The completed process.
    """
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env.pop("GIT_INDEX_FILE", None)
    return (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            env=env,
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
    """Return the staged diff of the real user index.

    Args:
        cwd: Repository directory.

    Returns:
        Output of ``git diff --cached``.
    """
    return _run(["git", "diff", "--cached"], cwd=cwd).stdout


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


def test_restore_ignores_paths_absent_from_checkpoint(tmp_path: Path) -> None:
    """A path that was never snapshotted must never be deleted by a restore."""
    repo = _init_git_repo(tmp_path)
    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()

    bystander = repo / "other.py"
    restore_checkpoint(checkpoint, [str(bystander)])  # type: ignore[arg-type]

    assert_that(bystander.exists()).is_true()
    assert_that(bystander.read_text(encoding="utf-8")).is_equal_to("keep = True\n")


def test_restore_accepts_absolute_target_paths(tmp_path: Path) -> None:
    """Absolute paths resolve to checkpoint entries instead of being dropped."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    tracked.write_text("alpha = 7\n", encoding="utf-8")

    restore_checkpoint(checkpoint, [str(tracked)])  # type: ignore[arg-type]

    assert_that(tracked.exists()).is_true()
    assert_that(tracked.read_text(encoding="utf-8")).is_equal_to("alpha = 1\n")


def test_restore_deletes_files_created_after_capture(tmp_path: Path) -> None:
    """Targets that did not exist at capture time are removed on restore."""
    repo = _init_git_repo(tmp_path)
    created = repo / "created.py"
    checkpoint = capture_checkpoint(["created.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    created.write_text("made = True\n", encoding="utf-8")

    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(created.exists()).is_false()


def test_directory_targets_skip_ignored_and_git_internals(tmp_path: Path) -> None:
    """Directory expansion honours .gitignore and never walks into .git/."""
    repo = _init_git_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored_dir = repo / "ignored"
    ignored_dir.mkdir()
    (ignored_dir / "junk.py").write_text("junk = 1\n", encoding="utf-8")

    checkpoint = capture_checkpoint(["."], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()

    paths = list(checkpoint.paths)  # type: ignore[union-attr]
    assert_that(paths).contains("tracked.py")
    assert_that([p for p in paths if p.startswith(".git/")]).is_empty()
    assert_that([p for p in paths if p.startswith("ignored/")]).is_empty()


def test_diff_ignores_paths_absent_from_checkpoint(tmp_path: Path) -> None:
    """Diffing an unsnapshotted path yields nothing rather than noise."""
    repo = _init_git_repo(tmp_path)
    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    (repo / "other.py").write_text("keep = 'changed'\n", encoding="utf-8")

    diff = diff_checkpoint(checkpoint, ["other.py"])  # type: ignore[arg-type]

    assert_that(diff).is_empty()


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


def test_report_checkpoints_announces_ref_when_files_changed(tmp_path: Path) -> None:
    """The post-run report names the ref only when the run changed something."""
    repo = _init_git_repo(tmp_path)
    suggestion = _make_suggestion("tracked.py", "alpha = 1\n", "alpha = 2\n")
    state = prepare_fix_batch([suggestion], repo, retention=10)
    assert_that(state).is_not_none()

    logger = MagicMock()
    _report_checkpoints([state], logger, is_json=False)
    assert_that(logger.console_output.call_count).is_equal_to(0)

    (repo / "tracked.py").write_text("alpha = 2\n", encoding="utf-8")
    _report_checkpoints([state], logger, is_json=False)
    assert_that(logger.console_output.call_count).is_equal_to(1)
    assert_that(logger.console_output.call_args.args[0]).contains(
        state.checkpoint.ref,  # type: ignore[union-attr]
    )


def test_report_checkpoints_silent_for_json(tmp_path: Path) -> None:
    """Machine-readable stdout never gains a checkpoint line."""
    repo = _init_git_repo(tmp_path)
    suggestion = _make_suggestion("tracked.py", "alpha = 1\n", "alpha = 2\n")
    state = prepare_fix_batch([suggestion], repo, retention=10)
    (repo / "tracked.py").write_text("alpha = 2\n", encoding="utf-8")

    logger = MagicMock()
    _report_checkpoints([state], logger, is_json=True)

    assert_that(logger.console_output.call_count).is_equal_to(0)


def test_restore_preserves_file_mode(tmp_path: Path) -> None:
    """A restored file keeps its permission bits, including the exec bit."""
    repo = _init_git_repo(tmp_path)
    script = repo / "script.sh"
    script.write_text("#!/bin/sh\necho one\n", encoding="utf-8")
    script.chmod(0o755)

    checkpoint = capture_checkpoint(["script.sh"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    script.write_text("#!/bin/sh\necho two\n", encoding="utf-8")
    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(script.read_text(encoding="utf-8")).contains("echo one")
    assert_that(script.stat().st_mode & 0o777).is_equal_to(0o755)


def test_restore_recreates_deleted_file_with_tree_mode(tmp_path: Path) -> None:
    """A target deleted after capture comes back with the checkpoint's mode."""
    repo = _init_git_repo(tmp_path)
    script = repo / "script.sh"
    script.write_text("#!/bin/sh\necho one\n", encoding="utf-8")
    script.chmod(0o755)

    checkpoint = capture_checkpoint(["script.sh"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    script.unlink()
    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(script.exists()).is_true()
    assert_that(script.stat().st_mode & 0o777).is_equal_to(0o755)


def test_diff_reports_edits_to_untracked_targets(tmp_path: Path) -> None:
    """An untracked target reads as modified, not deleted, in the diff."""
    repo = _init_git_repo(tmp_path)
    scratch = repo / "scratch.py"
    scratch.write_text("scratch = 1\n", encoding="utf-8")
    checkpoint = capture_checkpoint(["scratch.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    scratch.write_text("scratch = 2\n", encoding="utf-8")

    diff = diff_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(diff).contains("-scratch = 1")
    assert_that(diff).contains("+scratch = 2")
    assert_that(diff).does_not_contain("/dev/null")


def test_capture_prunes_before_writing_its_own_ref(tmp_path: Path) -> None:
    """With retention 1 the newest ref survives and older ones are gone."""
    repo = _init_git_repo(tmp_path)
    first = capture_checkpoint(
        ["tracked.py"],
        workspace_root=repo,
        run_id="1000-aaaaaaaa",
        keep=10,
    )
    second = capture_checkpoint(
        ["tracked.py"],
        workspace_root=repo,
        run_id="1001-bbbbbbbb",
        keep=1,
    )

    refs = list_checkpoint_refs(workspace_root=repo)
    assert_that(refs).is_equal_to([second.ref])  # type: ignore[union-attr]
    assert_that(refs).does_not_contain(first.ref)  # type: ignore[union-attr]


def test_capture_with_zero_retention_keeps_current_ref(tmp_path: Path) -> None:
    """``keep=0`` prunes history but never the ref the caller just got back."""
    repo = _init_git_repo(tmp_path)
    capture_checkpoint(
        ["tracked.py"],
        workspace_root=repo,
        run_id="1000-aaaaaaaa",
        keep=10,
    )
    current = capture_checkpoint(
        ["tracked.py"],
        workspace_root=repo,
        run_id="1001-bbbbbbbb",
        keep=0,
    )

    assert_that(current).is_not_none()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_equal_to(
        [current.ref],  # type: ignore[union-attr]
    )


def test_capture_ignores_ambient_git_index_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GIT_INDEX_FILE inherited from a git hook must not be honoured."""
    repo = _init_git_repo(tmp_path)
    hook_index = tmp_path / "hook-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(hook_index))

    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)

    assert_that(checkpoint).is_not_none()
    assert_that(hook_index.exists()).is_false()


def test_capture_and_restore_filename_with_glob_characters(tmp_path: Path) -> None:
    """Glob characters in a filename stay literal instead of matching magic."""
    repo = _init_git_repo(tmp_path)
    tricky = repo / "weird[1].py"
    tricky.write_text("value = 1\n", encoding="utf-8")

    checkpoint = capture_checkpoint(["weird[1].py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    assert_that(list(checkpoint.paths)).contains("weird[1].py")  # type: ignore[union-attr]

    tricky.write_text("value = 2\n", encoding="utf-8")
    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(tricky.read_text(encoding="utf-8")).is_equal_to("value = 1\n")


def test_target_deleted_before_capture_stays_deleted(tmp_path: Path) -> None:
    """A tracked target already gone at capture is not resurrected."""
    repo = _init_git_repo(tmp_path)
    tracked = repo / "tracked.py"
    tracked.unlink()

    checkpoint = capture_checkpoint(["tracked.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    tracked.write_text("alpha = 5\n", encoding="utf-8")

    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(tracked.exists()).is_false()


def test_restore_preserves_symlink_targets(tmp_path: Path) -> None:
    """A symlinked target is restored as a link, not flattened to a file."""
    repo = _init_git_repo(tmp_path)
    link = repo / "link.py"
    link.symlink_to("tracked.py")
    _run(["git", "add", "link.py"], cwd=repo)

    checkpoint = capture_checkpoint(["link.py"], workspace_root=repo, keep=10)
    assert_that(checkpoint).is_not_none()
    link.unlink()
    link.write_text("clobbered = True\n", encoding="utf-8")

    restore_checkpoint(checkpoint)  # type: ignore[arg-type]

    assert_that(link.is_symlink()).is_true()
    assert_that(os.readlink(link)).is_equal_to("tracked.py")
