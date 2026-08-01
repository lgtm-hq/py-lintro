"""Unit tests for the opt-in ``fmt`` git checkpoint hook (issue #1247)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.checkpoints import (
    CHECKPOINT_REF_PREFIX,
    capture_checkpoint,
    list_checkpoint_refs,
)
from lintro.api.pipeline import _capture_fmt_checkpoint
from lintro.enums.action import Action
from lintro.utils.execution.run_context import RunContext
from tests.unit.conftest import init_git_repo


@dataclass
class _RecordingLogger:
    """Console logger stub that records what would be printed."""

    lines: list[str] = field(default_factory=list)

    def console_output(self, text: str = "", **_kwargs: Any) -> None:
        """Record one console line.

        Args:
            text: The line that would be printed.
            **_kwargs: Styling arguments the real logger accepts.
        """
        self.lines.append(text)


@dataclass
class _AIConfigStub:
    """Minimal stand-in for the ``ai`` section of the config."""

    checkpoint_fmt: bool = True
    checkpoint_retention: int = 10


@dataclass
class _ConfigStub:
    """Minimal stand-in for the loaded lintro config."""

    ai: _AIConfigStub | None = field(default_factory=_AIConfigStub)


def _make_ctx(
    *,
    action: Action = Action.FIX,
    dry_run_preview: bool = False,
    checkpoint_fmt: bool = True,
    clean_stdout_output: bool = False,
    score_only: bool = False,
) -> RunContext:
    """Build a RunContext wired to a recording logger and a stub config.

    Args:
        action: The action the run executes.
        dry_run_preview: Whether this is a ``fmt --dry-run`` preview.
        checkpoint_fmt: Value of the ``ai.checkpoint_fmt`` toggle.
        clean_stdout_output: Whether stdout carries a machine document.
        score_only: Whether stdout carries only the numeric health score.

    Returns:
        RunContext: A context suitable for the checkpoint hook under test.
    """
    return RunContext(
        action=action,
        selection_action=Action.FIX,
        dry_run_preview=dry_run_preview,
        output_manager=None,
        logger=_RecordingLogger(),
        lintro_config=_ConfigStub(ai=_AIConfigStub(checkpoint_fmt=checkpoint_fmt)),
        clean_stdout_output=clean_stdout_output,
        score_only=score_only,
    )


def _init_git_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with one committed file.

    Args:
        tmp_path: Pytest temp directory.

    Returns:
        The repository root.
    """
    return init_git_repo(tmp_path, files={"tracked.py": "alpha = 1\n"})


def test_fmt_checkpoint_captured_and_announced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``checkpoint_fmt`` on, a fmt run captures and prints a ref."""
    repo = _init_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    ctx = _make_ctx()

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    refs = list_checkpoint_refs(workspace_root=repo)
    assert_that(refs).is_length(1)
    assert_that(ctx.logger.lines).is_length(1)
    assert_that(ctx.logger.lines[0]).contains(CHECKPOINT_REF_PREFIX)


def test_fmt_checkpoint_skipped_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hook is a no-op unless ``ai.checkpoint_fmt`` is enabled."""
    repo = _init_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    ctx = _make_ctx(checkpoint_fmt=False)

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert_that(ctx.logger.lines).is_empty()


def test_fmt_checkpoint_skipped_for_check_and_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is captured for a check run or a fmt dry-run preview."""
    repo = _init_git_repo(tmp_path)
    monkeypatch.chdir(repo)

    check_ctx = _make_ctx(action=Action.CHECK)
    _capture_fmt_checkpoint(ctx=check_ctx, paths=["tracked.py"])
    dry_ctx = _make_ctx(dry_run_preview=True)
    _capture_fmt_checkpoint(ctx=dry_ctx, paths=["tracked.py"])

    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()


def test_fmt_checkpoint_silent_outside_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a git work tree the hook captures nothing and stays quiet."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tracked.py").write_text("alpha = 1\n", encoding="utf-8")
    ctx = _make_ctx()

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    assert_that(ctx.logger.lines).is_empty()


def test_fmt_checkpoint_quiet_for_machine_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ref is still captured for JSON/score runs, but nothing is printed."""
    repo = _init_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    ctx = _make_ctx(clean_stdout_output=True)

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    assert_that(list_checkpoint_refs(workspace_root=repo)).is_length(1)
    assert_that(ctx.logger.lines).is_empty()


def test_fmt_checkpoint_quiet_for_score_only_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A score-only run captures a ref without polluting the score line."""
    repo = _init_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    ctx = _make_ctx(score_only=True)

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    assert_that(list_checkpoint_refs(workspace_root=repo)).is_length(1)
    assert_that(ctx.logger.lines).is_empty()


def test_fmt_checkpoint_resolves_paths_from_the_invocation_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative target is read from the cwd, not from the repository root."""
    repo = init_git_repo(
        tmp_path,
        files={"tracked.py": "alpha = 1\n", "pkg/tracked.py": "beta = 1\n"},
    )
    monkeypatch.chdir(repo / "pkg")
    ctx = _make_ctx()

    _capture_fmt_checkpoint(ctx=ctx, paths=["tracked.py"])

    checkpoint = capture_checkpoint(
        ["tracked.py"],
        workspace_root=repo / "pkg",
        keep=10,
    )
    assert_that(checkpoint).is_not_none()
    assert_that(list(checkpoint.paths)).is_equal_to(["pkg/tracked.py"])  # type: ignore[union-attr]
