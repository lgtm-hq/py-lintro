"""Tests for the AI undo patch writer."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion
from lintro.ai.undo import (
    UNDO_DIR,
    UNDO_FILE,
    prepare_fix_batch,
    restore_undo,
    save_undo_patch,
)


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


def test_file_fallback_never_writes_outside_the_workspace(tmp_path: Path) -> None:
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


def test_file_fallback_restore_preserves_mode(tmp_path: Path) -> None:
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
    script.write_text("#!/bin/sh\necho two\n", encoding="utf-8")
    restore_undo(state)  # type: ignore[arg-type]

    assert_that(script.read_text(encoding="utf-8")).contains("echo one")
    assert_that(script.stat().st_mode & 0o777).is_equal_to(0o755)
