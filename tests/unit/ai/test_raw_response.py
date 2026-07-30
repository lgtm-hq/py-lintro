"""Tests for durable capture of raw AI responses (#1853)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.ai.raw_response import (
    RAW_RESPONSE_DIR,
    describe_raw_response,
    persist_raw_response,
)

_LONG_PROSE = "Finding one. " + ("x" * 5000) + " Finding two."


def _persist(
    *,
    provider: str = "claude",
    stage: str = "cli-envelope",
    raw: str,
    workspace_root: Path,
) -> Path:
    """Persist a raw response and assert a path came back.

    Args:
        provider: Provider label for the capture name.
        stage: Stage label for the capture name.
        raw: The raw response text.
        workspace_root: Workspace root for the capture directory.

    Returns:
        The path the capture was written to.
    """
    path = persist_raw_response(
        provider=provider,
        stage=stage,
        raw=raw,
        workspace_root=workspace_root,
    )
    assert_that(path).is_not_none()
    return cast(Path, path)


def test_persist_writes_the_complete_response(tmp_path: Path) -> None:
    """The persisted file carries every character of the raw response."""
    path = _persist(
        provider="claude",
        stage="cli-envelope",
        raw=_LONG_PROSE,
        workspace_root=tmp_path,
    )

    assert_that(path.read_text(encoding="utf-8")).is_equal_to(_LONG_PROSE)
    assert_that(str(path)).contains(RAW_RESPONSE_DIR.replace("/", "/"))


def test_persist_falls_back_to_tempdir_when_workspace_is_unwritable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only workspace never costs the evidence."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "system-temp"
    monkeypatch.setattr(
        "lintro.ai.raw_response.tempfile.gettempdir",
        lambda: str(fallback),
    )

    path = _persist(
        provider="claude",
        stage="cli-envelope",
        raw="prose",
        workspace_root=blocked,
    )

    assert_that(path.read_text(encoding="utf-8")).is_equal_to("prose")
    assert_that(path.is_relative_to(fallback)).is_true()


def test_describe_embeds_the_untruncated_output(tmp_path: Path) -> None:
    """The evidence block never truncates, unlike the old ``stdout[:500]``."""
    block = describe_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw=_LONG_PROSE,
        workspace_root=tmp_path,
    )

    assert_that(block).contains(_LONG_PROSE)
    assert_that(block).contains("Finding two.")
    assert_that(block).contains(str(len(_LONG_PROSE)))


def test_describe_reports_the_saved_path(tmp_path: Path) -> None:
    """The evidence block names the file the response was saved to."""
    block = describe_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw="prose answer",
        workspace_root=tmp_path,
    )

    captured = list((tmp_path / RAW_RESPONSE_DIR).iterdir())
    assert_that(captured).is_length(1)
    assert_that(block).contains(str(captured[0]))


def test_evidence_block_neutralises_terminal_escapes(tmp_path: Path) -> None:
    """Untrusted output cannot drive the terminal it is printed to."""
    hostile = "Finding 1\x1b[2J\x1b]0;pwned\x07 and \x1b[31mred\x1b[0m text"

    block = describe_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw=hostile,
        workspace_root=tmp_path,
    )

    assert_that(block).does_not_contain("\x1b")
    assert_that(block).does_not_contain("\x07")
    assert_that(block).contains("Finding 1")
    assert_that(block).contains("red")


def test_capture_file_keeps_the_original_bytes(tmp_path: Path) -> None:
    """Sanitization is display-only; the capture stays byte-exact."""
    hostile = "Finding 1\x1b[2J done"

    describe_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw=hostile,
        workspace_root=tmp_path,
    )

    captured = list((tmp_path / RAW_RESPONSE_DIR).iterdir())
    assert_that(captured[0].read_text(encoding="utf-8")).is_equal_to(hostile)


def test_captures_are_owner_readable_only(tmp_path: Path) -> None:
    """Captures can embed diff context, so they are not world-readable."""
    path = _persist(
        provider="claude",
        stage="cli-envelope",
        raw="prose",
        workspace_root=tmp_path,
    )

    assert_that(path.stat().st_mode & 0o077).is_equal_to(0)
    assert_that(path.parent.stat().st_mode & 0o077).is_equal_to(0)


def test_repeated_identical_captures_do_not_fail(tmp_path: Path) -> None:
    """Two identical responses in the same second still both resolve a path."""
    first = _persist(raw="same prose", workspace_root=tmp_path)
    second = _persist(raw="same prose", workspace_root=tmp_path)

    assert_that(first.name).is_equal_to(second.name)
    assert_that(second.read_text(encoding="utf-8")).is_equal_to("same prose")


def test_symlinked_capture_directory_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink planted at the capture path never receives captures."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    (workspace / ".lintro-cache" / "ai").mkdir(parents=True)
    (workspace / RAW_RESPONSE_DIR).symlink_to(elsewhere)
    fallback = tmp_path / "system-temp"
    monkeypatch.setattr(
        "lintro.ai.raw_response.tempfile.gettempdir",
        lambda: str(fallback),
    )

    path = _persist(raw="prose", workspace_root=workspace)

    assert_that(path.is_relative_to(fallback)).is_true()
    assert_that(list(elsewhere.iterdir())).is_empty()


def test_loose_directory_permissions_are_tightened(tmp_path: Path) -> None:
    """A pre-existing capture directory with loose modes is made owner-only."""
    directory = tmp_path / RAW_RESPONSE_DIR
    directory.mkdir(parents=True)
    directory.chmod(0o755)

    path = _persist(raw="prose", workspace_root=tmp_path)

    assert_that(path.parent.stat().st_mode & 0o077).is_equal_to(0)


def test_persisted_names_are_filesystem_safe(tmp_path: Path) -> None:
    """Provider and stage labels are slugged, never written raw."""
    path = _persist(
        provider="Cursor agent",
        stage="CLI/envelope",
        raw="prose",
        workspace_root=tmp_path,
    )

    assert_that(path.name).starts_with("cli-envelope-cursor-agent-")
    assert_that(path.name).ends_with(".txt")
