"""Tests for AI undo patches and checkpoint fallback rollback."""

from __future__ import annotations

import subprocess  # nosec B404 - exercise git plumbing failures in regression tests
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.checkpoints import (
    CheckpointError,
    capture_checkpoint,
    git_checkpoints_available,
    list_checkpoint_refs,
)
from lintro.ai.models import AIFixSuggestion
from lintro.ai.undo import (
    UNDO_DIR,
    UNDO_FILE,
    prepare_fix_batch,
    restore_undo,
    save_undo_patch,
)
from tests.conftest import run_git
from tests.unit.conftest import init_git_repo


def _make_suggestion(
    file: str = "src/app.py",
    original: str = "x = 1\n",
    suggested: str = "x = 2\n",
) -> AIFixSuggestion:
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


def test_saves_patch_file(tmp_path: Path) -> None:
    """save_undo_patch creates a patch file on disk."""
    s = _make_suggestion()
    result = save_undo_patch([s], workspace_root=tmp_path)

    expected_path = tmp_path / UNDO_DIR / UNDO_FILE
    assert_that(expected_path.exists()).is_true()
    assert_that(result).is_equal_to(expected_path)


def test_returns_path(tmp_path: Path) -> None:
    """save_undo_patch returns the path to the patch file."""
    s = _make_suggestion()
    result = save_undo_patch([s], workspace_root=tmp_path)
    assert_that(result).is_not_none()
    assert_that(str(result)).ends_with(UNDO_FILE)


def test_reverse_diff_suggested_to_original(tmp_path: Path) -> None:
    """The patch is a reverse diff (suggested -> original) for undo."""
    s = _make_suggestion(original="old_line\n", suggested="new_line\n")
    result = save_undo_patch([s], workspace_root=tmp_path)
    assert_that(result).is_not_none()

    content = result.read_text()  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    # In a reverse diff, the "from" shows the suggested (new) code
    # and the "to" shows the original (old) code
    assert_that(content).contains("-new_line")
    assert_that(content).contains("+old_line")


def test_empty_list_returns_none(tmp_path: Path) -> None:
    """An empty suggestion list returns None."""
    result = save_undo_patch([], workspace_root=tmp_path)
    assert_that(result).is_none()


def test_patch_content_is_valid_unified_diff(tmp_path: Path) -> None:
    """The patch content contains standard unified diff markers."""
    s = _make_suggestion(original="alpha\n", suggested="beta\n")
    result = save_undo_patch([s], workspace_root=tmp_path)
    assert_that(result).is_not_none()

    content = result.read_text()  # type: ignore[union-attr]  # assertpy is_not_none narrows this
    assert_that(content).contains("---")
    assert_that(content).contains("+++")
    assert_that(content).contains("@@")


def test_file_fallback_never_writes_outside_the_workspace(
    tmp_path: Path,
    no_git_checkpoints: None,
) -> None:
    """An escaping path is dropped at capture and never restored."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outsider = tmp_path / "outside.py"
    outsider.write_text("secret = 1\n", encoding="utf-8")
    suggestion = AIFixSuggestion(
        file="../outside.py",
        line=1,
        code="E001",
        original_code="secret = 1\n",
        suggested_code="secret = 2\n",
    )

    state = prepare_fix_batch([suggestion], workspace)

    assert_that(state).is_not_none()
    assert_that(state.file_snapshot.contents).is_empty()  # type: ignore[union-attr]

    outsider.write_text("secret = 999\n", encoding="utf-8")
    restore_undo(state)  # type: ignore[arg-type]

    assert_that(outsider.exists()).is_true()
    assert_that(outsider.read_text(encoding="utf-8")).is_equal_to("secret = 999\n")


def test_file_fallback_restore_preserves_mode(
    tmp_path: Path,
    no_git_checkpoints: None,
) -> None:
    """The non-git fallback keeps the executable bit on restore."""
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho one\n", encoding="utf-8")
    script.chmod(0o755)
    suggestion = AIFixSuggestion(
        file="run.sh",
        line=1,
        code="E001",
        original_code="echo one\n",
        suggested_code="echo two\n",
    )

    state = prepare_fix_batch([suggestion], tmp_path)
    assert_that(state).is_not_none()
    # Simulate a formatter that rewrites the file and drops the exec bit.
    script.write_text("#!/bin/sh\necho two\n", encoding="utf-8")
    script.chmod(0o600)
    restore_undo(state)  # type: ignore[arg-type]

    assert_that(script.read_text(encoding="utf-8")).contains("echo one")
    assert_that(script.stat().st_mode & 0o777).is_equal_to(0o755)


@pytest.mark.parametrize(
    "checkpoint_error",
    [None, CheckpointError("git ls-files failed: enumeration failed")],
    ids=["capture-returned-none", "checkpoint-error"],
)
def test_checkpoint_failure_falls_back_to_restorable_file_snapshot(
    tmp_path: Path,
    checkpoint_error: CheckpointError | None,
) -> None:
    """Fallback restores actual pre-batch bytes, modes and missing paths."""
    repo = init_git_repo(tmp_path, files={"run.sh": "#!/bin/sh\necho committed\n"})
    script = repo / "run.sh"
    original = b"#!/bin/sh\r\necho 'unsaved user edit'\r\n"
    script.write_bytes(original)
    script.chmod(0o755)
    created = repo / "created.py"
    suggestions = [
        _make_suggestion(file="run.sh", original="stale suggestion\n"),
        _make_suggestion(file="created.py", original=""),
    ]

    with patch(
        "lintro.ai.undo.capture_checkpoint",
        return_value=None,
        side_effect=checkpoint_error,
    ):
        state = prepare_fix_batch(suggestions, repo)

    assert state is not None
    assert_that(state.kind).is_equal_to("file")
    assert_that(state.checkpoint).is_none()
    assert_that(script.read_bytes()).is_equal_to(original)
    script.write_bytes(b"#!/bin/sh\necho fixed\n")
    script.chmod(0o600)
    created.write_text("created = True\n", encoding="utf-8")

    restore_undo(state)

    assert_that(script.read_bytes()).is_equal_to(original)
    assert_that(script.stat().st_mode & 0o777).is_equal_to(0o755)
    assert_that(created.exists()).is_false()


@pytest.mark.parametrize("failure", ["exit", "oserror", "timeout"])
def test_directory_expansion_failure_reaches_file_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Capture rejects subprocess-injected listing failures; undo restores bytes."""
    repo = init_git_repo(
        tmp_path,
        files={"target.py": "committed = True\n", "pkg/other.py": "keep = True\n"},
    )
    previous = capture_checkpoint(["target.py"], workspace_root=repo)
    assert previous is not None
    target = repo / "target.py"
    original = b"unsaved = True\r\n"
    target.write_bytes(original)
    real_run = subprocess.run

    def fail_directory_listing(
        args: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        if args[1:2] == ["ls-files"] and args[-1] == "pkg":
            if failure == "oserror":
                raise OSError("enumeration failed")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 60.0)
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="pkg/other.py\0",
                stderr="enumeration failed",
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr("lintro.ai.checkpoints.subprocess.run", fail_directory_listing)
    state = prepare_fix_batch(
        [_make_suggestion(file="target.py"), _make_suggestion(file="pkg")],
        repo,
        retention=1,
    )

    assert state is not None
    assert_that(state.kind).is_equal_to("file")
    assert_that(state.checkpoint).is_none()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_equal_to([previous.ref])
    assert_that(target.read_bytes()).is_equal_to(original)
    target.write_bytes(b"fixed = True\n")

    restore_undo(state)

    assert_that(target.read_bytes()).is_equal_to(original)
    assert_that((repo / "pkg/other.py").read_text(encoding="utf-8")).is_equal_to(
        "keep = True\n",
    )


@pytest.mark.parametrize("corrupt_index", [False, True], ids=["valid", "corrupt"])
def test_real_git_index_controls_checkpoint_fallback(
    tmp_path: Path,
    corrupt_index: bool,
) -> None:
    """Real ls-files failure selects file rollback; a valid index selects git."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = init_git_repo(
        repo_path,
        files={"target.py": "committed = True\n", "pkg/other.py": "keep = True\n"},
    )
    target = repo / "target.py"
    original = b"unsaved = True\r\n"
    target.write_bytes(original)
    # This index belongs only to the repository created by this test.
    index = repo / ".git" / "index"
    if corrupt_index:
        index.write_bytes(b"invalid git index\n")
    index_before = index.read_bytes()
    paths = ["target.py", "pkg"]
    listing_command = [
        "git",
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
        "--",
        "pkg",
    ]

    assert_that(git_checkpoints_available(repo)).is_true()
    if corrupt_index:
        with pytest.raises(subprocess.CalledProcessError) as failure:
            run_git(listing_command, cwd=repo)
        assert_that(failure.value.returncode).is_not_equal_to(0)
        assert_that(failure.value.stderr).contains("index")
        with pytest.raises(CheckpointError, match=r"git ls-files .*failed: .*index"):
            capture_checkpoint(paths, workspace_root=repo)
    else:
        listing = run_git(listing_command, cwd=repo)
        assert_that(listing.returncode).is_equal_to(0)
        assert_that(listing.stdout).is_equal_to("pkg/other.py\0")

    state = prepare_fix_batch([_make_suggestion(file=path) for path in paths], repo)

    assert state is not None
    assert_that(state.kind).is_equal_to("file" if corrupt_index else "git")
    if corrupt_index:
        assert_that(state.checkpoint).is_none()
        assert state.file_snapshot is not None
        assert_that(state.file_snapshot.contents["target.py"]).is_equal_to(original)
        assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    else:
        assert state.checkpoint is not None
        assert_that(state.checkpoint.paths).contains("target.py", "pkg/other.py")
    assert_that(target.read_bytes()).is_equal_to(original)
    target.write_bytes(b"fixed = True\n")

    restore_undo(state)

    assert_that(target.read_bytes()).is_equal_to(original)
    assert_that(index.read_bytes()).is_equal_to(index_before)
    assert_that((repo / "pkg/other.py").read_bytes()).is_equal_to(b"keep = True\n")


@pytest.mark.parametrize(
    "capture_error",
    [CheckpointError("git checkpoint failed"), OSError("checkpoint index denied")],
    ids=["checkpoint-error", "oserror"],
)
def test_checkpoint_error_with_unreadable_snapshot_aborts_prepare(
    tmp_path: Path,
    capture_error: CheckpointError | OSError,
) -> None:
    """A capture error cannot let a batch proceed without a usable snapshot."""
    repo = init_git_repo(tmp_path, files={"target.py": "original = True\n"})
    suggestion = _make_suggestion(file="target.py")
    target = repo / "target.py"
    bystander = repo / "bystander.py"
    bystander.write_bytes(b"readable = True\n")
    real_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == target:
            raise PermissionError("snapshot denied")
        return real_read_bytes(path)

    with (
        patch(
            "lintro.ai.undo.capture_checkpoint",
            side_effect=capture_error,
        ),
        patch.object(Path, "read_bytes", read_bytes),
    ):
        assert_that(bystander.read_bytes()).is_equal_to(b"readable = True\n")
        with pytest.raises(PermissionError, match="snapshot denied"):
            prepare_fix_batch([suggestion], repo)

    assert_that(target.read_text(encoding="utf-8")).is_equal_to(
        "original = True\n",
    )
    assert_that((repo / UNDO_DIR / UNDO_FILE).exists()).is_false()


def test_git_index_tempfile_failure_falls_back_to_file_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError allocating the checkpoint index leaves file rollback usable."""
    repo = init_git_repo(tmp_path, files={"target.py": "committed = True\n"})
    target = repo / "target.py"
    original = b"unsaved = True\r\n"
    target.write_bytes(original)
    real_mkstemp = tempfile.mkstemp

    def fail_git_index(*args: Any, **kwargs: Any) -> tuple[int, str]:
        if kwargs.get("prefix") == "lintro-git-index-":
            raise OSError("checkpoint index denied")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("lintro.ai.checkpoints.tempfile.mkstemp", fail_git_index)
    state = prepare_fix_batch([_make_suggestion(file="target.py")], repo)

    assert state is not None
    assert_that(state.kind).is_equal_to("file")
    assert_that(state.checkpoint).is_none()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert state.patch_path is not None
    assert_that(state.patch_path.read_text(encoding="utf-8")).contains("---", "+++")
    assert_that(target.read_bytes()).is_equal_to(original)
    target.write_bytes(b"fixed = True\n")

    restore_undo(state)

    assert_that(target.read_bytes()).is_equal_to(original)


@pytest.mark.parametrize(
    "use_git",
    [False, True],
    ids=["file-snapshot", "git-checkpoint"],
)
def test_legacy_patch_tempfile_failure_preserves_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_git: bool,
) -> None:
    """An optional reverse-patch failure cannot discard either rollback backend."""
    workspace = (
        init_git_repo(tmp_path, files={"target.py": "original = True\n"})
        if use_git
        else tmp_path
    )
    target = workspace / "target.py"
    target.write_bytes(b"user_edit = True\n")
    real_mkstemp = tempfile.mkstemp

    def fail_legacy_patch(*args: Any, **kwargs: Any) -> tuple[int, str]:
        if kwargs.get("dir") == workspace / UNDO_DIR:
            raise OSError("legacy patch denied")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("lintro.ai.undo.tempfile.mkstemp", fail_legacy_patch)
    state = prepare_fix_batch([_make_suggestion(file="target.py")], workspace)

    assert state is not None
    assert_that(state.kind).is_equal_to("git" if use_git else "file")
    assert_that(state.patch_path).is_none()
    assert_that(list((workspace / UNDO_DIR).iterdir())).is_empty()
    target.write_bytes(b"fixed = True\n")

    restore_undo(state)

    assert_that(target.read_bytes()).is_equal_to(b"user_edit = True\n")


@pytest.fixture
def no_git_checkpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the file-snapshot backend regardless of where tmp_path lives.

    ``prepare_fix_batch`` prefers git whenever the workspace is inside a work
    tree, so a ``TMPDIR`` under one would silently skip the fallback these
    tests exist to cover.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.ai.undo.git_checkpoints_available",
        lambda _workspace_root: False,
    )
