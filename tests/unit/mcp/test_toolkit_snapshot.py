"""Tests for the snapshot/diff/restore primitives behind ``lintro_format``."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.mcp.errors import McpError
from lintro.mcp.toolkits import snapshot as snapshot_module
from lintro.mcp.toolkits.snapshot import changes_since, restore, snapshot_files


def test_snapshot_captures_files_the_tool_can_reach(tmp_path: Path) -> None:
    """Discovery drives the snapshot, so unrelated file types stay out of it."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    captured = snapshot_files(
        paths=[str(tmp_path)],
        tool_names=["ruff"],
    )

    assert_that(set(captured)).is_equal_to({(tmp_path / "a.py").resolve()})


def test_snapshot_ignores_an_unknown_tool_name(tmp_path: Path) -> None:
    """A tool that cannot be resolved contributes no files rather than raising."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    captured = snapshot_files(
        paths=[str(tmp_path)],
        tool_names=["not-a-real-linter"],
    )

    assert_that(captured).is_empty()


def test_snapshot_refuses_to_exceed_its_memory_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized tree fails before any tool runs, with a structured error."""
    (tmp_path / "a.py").write_text("x = 1\n" * 100, encoding="utf-8")
    monkeypatch.setattr(snapshot_module, "MAX_SNAPSHOT_BYTES", 8)

    with pytest.raises(McpError) as excinfo:
        snapshot_files(
            paths=[str(tmp_path)],
            tool_names=["ruff"],
        )

    assert_that(str(excinfo.value.code)).is_equal_to("execution_error")
    assert_that(excinfo.value.envelope.detail).contains_entry(
        {"reason": "snapshot_too_large"},
    )


def test_snapshot_fails_loudly_when_a_candidate_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable candidate aborts the run rather than becoming unrestorable."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    def _boom(self: Path) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_bytes", _boom)

    with pytest.raises(McpError) as excinfo:
        snapshot_files(paths=[str(tmp_path)], tool_names=["ruff"])

    assert_that(excinfo.value.envelope.detail).contains_entry(
        {"reason": "snapshot_read_failed"},
    )


def test_changes_since_fails_loudly_when_a_file_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated change report is reported as an error, not returned quietly."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    captured = {target.resolve(): b"x = 0\n"}

    def _boom(self: Path) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_bytes", _boom)

    with pytest.raises(McpError) as excinfo:
        changes_since(snapshot=captured, workspace=tmp_path)

    assert_that(excinfo.value.envelope.detail).contains_entry(
        {"reason": "diff_read_failed"},
    )


def test_changes_since_reports_a_unified_diff_for_modified_files(
    tmp_path: Path,
) -> None:
    """A modified file yields a diff with workspace-relative headers."""
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    captured = {target.resolve(): target.read_bytes()}
    target.write_text("x = 2\n", encoding="utf-8")

    changes = changes_since(snapshot=captured, workspace=tmp_path)

    assert_that(changes).is_length(1)
    assert_that(changes[0].path).is_equal_to("pkg/a.py")
    assert_that(changes[0].diff).contains("--- a/pkg/a.py", "-x = 1", "+x = 2")


def test_changes_since_ignores_untouched_files(tmp_path: Path) -> None:
    """A file nothing rewrote produces no change entry."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    captured = {target.resolve(): target.read_bytes()}

    assert_that(changes_since(snapshot=captured, workspace=tmp_path)).is_empty()


def test_changes_since_notes_binary_files_instead_of_diffing_them(
    tmp_path: Path,
) -> None:
    """Undecodable content is reported as a note, not a mangled diff."""
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00")
    captured = {target.resolve(): target.read_bytes()}
    target.write_bytes(b"\xff\xfe\x01")

    changes = changes_since(snapshot=captured, workspace=tmp_path)

    assert_that(changes[0].diff).contains("Binary files differ")


def test_changes_since_treats_a_deleted_file_as_a_removal(tmp_path: Path) -> None:
    """A file the run deleted diffs against empty rather than being skipped."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    captured = {target.resolve(): target.read_bytes()}
    target.unlink()

    changes = changes_since(snapshot=captured, workspace=tmp_path)

    assert_that(changes[0].diff).contains("-x = 1")


def test_restore_rewrites_changed_files_and_recreates_deleted_ones(
    tmp_path: Path,
) -> None:
    """Restoring returns every captured file to its snapshot bytes."""
    modified = tmp_path / "a.py"
    deleted = tmp_path / "b.py"
    modified.write_text("x = 1\n", encoding="utf-8")
    deleted.write_text("y = 2\n", encoding="utf-8")
    captured = {
        modified.resolve(): modified.read_bytes(),
        deleted.resolve(): deleted.read_bytes(),
    }
    modified.write_text("x = 999\n", encoding="utf-8")
    deleted.unlink()

    restore(snapshot=captured)

    assert_that(modified.read_text(encoding="utf-8")).is_equal_to("x = 1\n")
    assert_that(deleted.read_text(encoding="utf-8")).is_equal_to("y = 2\n")


def test_restore_leaves_untouched_files_alone(tmp_path: Path) -> None:
    """An unmodified file is not rewritten, so its mtime survives."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    captured = {target.resolve(): target.read_bytes()}
    before_mtime = target.stat().st_mtime_ns

    restore(snapshot=captured)

    assert_that(target.stat().st_mtime_ns).is_equal_to(before_mtime)
