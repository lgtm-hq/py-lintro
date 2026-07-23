"""Tests for lintro.ai.rerun module."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.rerun import (
    absolute_paths_for_context,
    apply_rerun_results,
    rerun_tools,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

from .conftest import MockIssue

_ByTool = dict[str, tuple[ToolResult, list[BaseIssue]]]


# -- TestAbsolutePathsForContext: Tests for absolute_paths_for_context. ------


def test_absolute_paths_for_context_resolves_relative_paths(tmp_path: Path) -> None:
    """Relative paths are resolved to absolute form for cwd-explicit rerun."""
    child = tmp_path / "src" / "main.py"
    child.parent.mkdir(parents=True)
    child.write_text("x = 1\n", encoding="utf-8")

    monkeyed_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = absolute_paths_for_context(file_paths=["src/main.py"])
    finally:
        os.chdir(monkeyed_cwd)

    assert_that(result).is_length(1)
    assert_that(result[0]).is_equal_to(str(child.resolve()))
    assert_that(Path(result[0]).is_absolute()).is_true()


def test_absolute_paths_for_context_keeps_absolute_paths(tmp_path: Path) -> None:
    """Already-absolute paths are returned in resolved absolute form."""
    absolute = str(tmp_path / "a" / "b.py")

    result = absolute_paths_for_context(file_paths=[absolute])

    assert_that(result).is_length(1)
    assert_that(result[0]).is_equal_to(str(Path(absolute).resolve()))


# -- TestRerunNoChdir: rerun must never mutate the process-global cwd. -------


@patch("lintro.tools.tool_manager.get_tool")
def test_ai_rerun_does_not_chdir(
    mock_get_tool: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rerun_tools must not call os.chdir; the process cwd stays unchanged."""

    def _fail_chdir(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("rerun_tools must not call os.chdir")

    monkeypatch.setattr(os, "chdir", _fail_chdir)

    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")

    issue = MockIssue(
        file=str(source),
        line=1,
        column=1,
        message="test",
        code="E501",
        severity="warning",
    )
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[issue],
        cwd=str(tmp_path),
    )
    by_tool: _ByTool = {"ruff": (result, [issue])}

    captured: dict[str, Any] = {}

    class _FakeTool:
        def check(self, paths: Any, options: Any) -> ToolResult:
            captured["paths"] = paths
            return ToolResult(name="ruff", success=True, issues_count=0, issues=[])

    mock_get_tool.return_value = _FakeTool()

    before = os.getcwd()
    results = rerun_tools(by_tool)
    after = os.getcwd()

    assert_that(after).is_equal_to(before)
    assert_that(results).is_length(1)
    # Tool receives absolute paths so it derives its own cwd.
    assert_that(captured["paths"]).is_equal_to([str(source.resolve())])


@patch("lintro.plugins.base.run_subprocess")
def test_ai_rerun_passes_cwd_to_subprocess(
    mock_run_subprocess: MagicMock,
    tmp_path: Path,
) -> None:
    """The stubbed subprocess layer receives the intended cwd during rerun.

    A real tool (ruff) is re-run through ``rerun_tools`` with the subprocess
    layer stubbed. Because rerun passes absolute paths, the tool resolves its
    working directory from the target file's parent and hands that cwd to
    ``run_subprocess`` — no process-global ``os.chdir`` involved.
    """
    from lintro.plugins.subprocess_executor import SubprocessResult

    source = tmp_path / "main.py"
    source.write_text("x = 1\n", encoding="utf-8")
    expected_cwd = str(source.resolve().parent)

    captured_cwds: list[str | None] = []

    def _fake_run_subprocess(
        cmd: list[str],
        timeout: float,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        captured_cwds.append(cwd)
        return SubprocessResult(returncode=0, stdout="", stderr="", output="")

    mock_run_subprocess.side_effect = _fake_run_subprocess

    issue = MockIssue(
        file=str(source),
        line=1,
        column=1,
        message="test",
        code="E501",
        severity="warning",
    )
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[issue],
        cwd=expected_cwd,
    )
    by_tool: _ByTool = {"ruff": (result, [issue])}

    results = rerun_tools(by_tool)

    assert_that(results).is_length(1)
    assert_that(captured_cwds).is_not_empty()
    assert_that(captured_cwds).contains(expected_cwd)


# -- TestRerunTools: Tests for rerun_tools. ----------------------------------


def test_rerun_tools_with_missing_tools() -> None:
    """When tool_manager.get_tool() raises KeyError, the tool is skipped."""
    issue = MockIssue(
        file="src/main.py",
        line=1,
        column=1,
        message="test",
        code="T001",
        severity="low",
    )
    result = ToolResult(name="missing_tool", success=False, issues_count=1)
    by_tool: _ByTool = {"missing_tool": (result, [issue])}

    mock_tool_manager = MagicMock()
    mock_tool_manager.get_tool.side_effect = KeyError("missing_tool")

    with patch("lintro.tools.tool_manager", mock_tool_manager):
        results = rerun_tools(by_tool)

    assert_that(results).is_not_none()
    assert_that(results).is_empty()


# -- TestApplyRerunResults: Tests for apply_rerun_results. -------------------


def test_apply_rerun_results_preserves_native_counters() -> None:
    """Native initial/fixed counts are preserved after rerun."""
    original_result = ToolResult(
        name="ruff",
        success=False,
        issues_count=10,
        initial_issues_count=10,
        fixed_issues_count=7,
        remaining_issues_count=3,
    )
    issue = MockIssue(
        file="src/main.py",
        line=1,
        column=1,
        message="remaining issue",
        code="E501",
        severity="warning",
    )
    by_tool: _ByTool = {"ruff": (original_result, [issue])}

    rerun_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=2,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                column=1,
                message="issue 1",
                code="E501",
                severity="warning",
            ),
            MockIssue(
                file="src/main.py",
                line=5,
                column=1,
                message="issue 2",
                code="E502",
                severity="warning",
            ),
        ],
    )

    apply_rerun_results(by_tool=by_tool, rerun_results=[rerun_result])

    assert_that(original_result.initial_issues_count).is_equal_to(10)
    assert_that(original_result.fixed_issues_count).is_equal_to(7)


def test_apply_rerun_results_updates_remaining_issues() -> None:
    """Remaining issues are updated from rerun results."""
    original_result = ToolResult(
        name="ruff",
        success=False,
        issues_count=5,
        initial_issues_count=5,
        fixed_issues_count=3,
        remaining_issues_count=2,
    )
    issue = MockIssue(
        file="src/main.py",
        line=1,
        column=1,
        message="old issue",
        code="E501",
        severity="warning",
    )
    by_tool: _ByTool = {"ruff": (original_result, [issue])}

    refreshed_issue = MockIssue(
        file="src/main.py",
        line=10,
        column=1,
        message="remaining issue",
        code="E501",
        severity="warning",
    )
    rerun_result = ToolResult(
        name="ruff",
        success=True,
        issues_count=1,
        issues=[refreshed_issue],
    )

    apply_rerun_results(by_tool=by_tool, rerun_results=[rerun_result])

    assert_that(original_result.remaining_issues_count).is_equal_to(1)
    assert_that(original_result.issues_count).is_equal_to(1)
    assert_that(original_result.issues).is_length(1)
    assert_that(original_result.issues).is_not_none()
    assert_that(original_result.issues[0].message).is_equal_to("remaining issue")  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(original_result.success).is_true()
