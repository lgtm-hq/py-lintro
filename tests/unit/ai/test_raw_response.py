"""Tests for durable capture of raw AI responses (#1853)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.raw_response import (
    RAW_RESPONSE_DIR,
    describe_raw_response,
    persist_raw_response,
)

_LONG_PROSE = "Finding one. " + ("x" * 5000) + " Finding two."


def test_persist_writes_the_complete_response(tmp_path: Path) -> None:
    """The persisted file carries every character of the raw response."""
    path = persist_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw=_LONG_PROSE,
        workspace_root=tmp_path,
    )

    assert_that(path).is_not_none()
    assert_that(path.read_text(encoding="utf-8")).is_equal_to(_LONG_PROSE)
    assert_that(str(path)).contains(RAW_RESPONSE_DIR.replace("/", "/"))


def test_persist_falls_back_to_tempdir_when_workspace_is_unwritable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only workspace never costs the evidence."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    path = persist_raw_response(
        provider="claude",
        stage="cli-envelope",
        raw="prose",
        workspace_root=blocked,
    )

    assert_that(path).is_not_none()
    assert_that(path.read_text(encoding="utf-8")).is_equal_to("prose")


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


def test_persisted_names_are_filesystem_safe(tmp_path: Path) -> None:
    """Provider and stage labels are slugged, never written raw."""
    path = persist_raw_response(
        provider="Cursor agent",
        stage="CLI/envelope",
        raw="prose",
        workspace_root=tmp_path,
    )

    assert_that(path.name).starts_with("cli-envelope-cursor-agent-")
    assert_that(path.name).ends_with(".txt")
