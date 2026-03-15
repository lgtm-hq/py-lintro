"""Tests for lintro.ai.rerun module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.rerun import (
    _tool_cwd,
    apply_rerun_results,
    paths_for_context,
    rerun_tools,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

from .conftest import MockIssue

_ByTool = dict[str, tuple[ToolResult, list[BaseIssue]]]


# -- TestToolCwd: Tests for _tool_cwd context manager. -----------------------


def test_tool_cwd_restores_original_cwd(tmp_path: Path) -> None:
    """_tool_cwd changes cwd and restores it after exit."""
    original = os.getcwd()
    target = str(tmp_path)

    with _tool_cwd(target):
        assert_that(os.getcwd()).is_equal_to(target)

    assert_that(os.getcwd()).is_equal_to(original)


def test_tool_cwd_skips_when_none() -> None:
    """When cwd is None, the context manager yields without changing directory."""
    original = os.getcwd()

    with _tool_cwd(None):
        assert_that(os.getcwd()).is_equal_to(original)

    assert_that(os.getcwd()).is_equal_to(original)


# -- TestPathsForContext: Tests for paths_for_context. -----------------------


def test_paths_for_context_relativizes_paths(tmp_path: Path) -> None:
    """Given absolute paths and a cwd, returns relative paths for children."""
    cwd = str(tmp_path)
    child = str(tmp_path / "src" / "main.py")
    outside = "/some/other/path/file.py"

    result = paths_for_context(file_paths=[child, outside], cwd=cwd)

    assert_that(result).is_length(2)
    assert_that(result[0]).is_equal_to(str(Path("src") / "main.py"))
    # Outside path stays absolute (resolved)
    assert_that(result[1]).is_equal_to(str(Path(outside).resolve()))


def test_paths_for_context_returns_originals_when_no_cwd() -> None:
    """When cwd is None, returns original paths unchanged."""
    paths = ["/a/b/c.py", "/d/e/f.py"]

    result = paths_for_context(file_paths=paths, cwd=None)

    assert_that(result).is_equal_to(paths)


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
