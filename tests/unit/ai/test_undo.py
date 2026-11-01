"""Tests for the AI undo patch writer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.checkpoints import CheckpointError
from lintro.ai.models import AIFixSuggestion
from lintro.ai.undo import (
    UNDO_DIR,
    UNDO_FILE,
    prepare_fix_batch,
    restore_undo,
    save_undo_patch,
)
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
    ids=["unavailable", "checkpoint-error"],
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


def test_checkpoint_error_with_unreadable_snapshot_aborts_prepare(
    tmp_path: Path,
) -> None:
    """A capture error cannot let a batch proceed without a usable snapshot."""
    repo = init_git_repo(tmp_path, files={"target.py": "original = True\n"})
    suggestion = _make_suggestion(file="target.py")

    with (
        patch(
            "lintro.ai.undo.capture_checkpoint",
            side_effect=CheckpointError("git checkpoint failed"),
        ),
        patch.object(
            Path,
            "read_bytes",
            side_effect=PermissionError("snapshot denied"),
        ),
        pytest.raises(PermissionError, match="snapshot denied"),
    ):
        prepare_fix_batch([suggestion], repo)

    assert_that((repo / "target.py").read_text(encoding="utf-8")).is_equal_to(
        "original = True\n",
    )


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
