"""Baseline filtering preserves callbacks independently of console rendering."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.execution.run_context import RunContext
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import change_baseline


@pytest.fixture
def runner_artifact(
    tmp_path: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    output_format: str,
    monkeypatch: pytest.MonkeyPatch,
) -> RunArtifact:
    """Wire the simple runner to an execution double that honors its callback."""
    baseline = snapshot([dirty_result], root=tmp_path)
    change_baseline(tmp_path / "baseline.json", lambda _: baseline, create=True)
    result = replace(
        dirty_result,
        issues=[finding, replace(finding, code="F821", message="New undefined value")],
        issues_count=2,
    )
    artifact = RunArtifact(
        action=Action.CHECK,
        workspace_root=tmp_path,
        tool_results=[result],
        total_issues=2,
        total_remaining=2,
        exit_code=1,
    )
    logger = MagicMock()
    logger.console_output.side_effect = lambda text="", **_kwargs: print(text)
    ctx = RunContext(
        action=Action.CHECK,
        selection_action=Action.CHECK,
        dry_run_preview=False,
        output_manager=MagicMock(),
        logger=logger,
        lintro_config=LintroConfig(),
        clean_stdout_output=output_format == "json",
    )

    def execute(
        *,
        on_tool_result: Callable[[ToolResult], None] | None,
        **_kwargs: Any,
    ) -> RunArtifact:
        if on_tool_result is not None:
            for tool_result in artifact.tool_results:
                on_tool_result(tool_result)
        return artifact

    monkeypatch.setattr(te, "build_run_context", lambda **_kwargs: ctx)
    monkeypatch.setattr(te, "execute_run", execute)
    return artifact


def _run(
    *,
    output_format: str,
    baseline: Path | None,
    on_tool_result: Callable[[ToolResult], None] | None = None,
) -> int:
    """Run only the wrapper's per-tool callback and baseline handling."""
    return te.run_lint_tools_simple(
        action=Action.CHECK,
        paths=["."],
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format=output_format,
        verbose=False,
        baseline=str(baseline) if baseline is not None else None,
        on_tool_result=on_tool_result,
        render_summary=False,
    )


@pytest.mark.parametrize("output_format", ["grid", "json"])
@pytest.mark.parametrize("baseline_enabled", [False, True])
def test_custom_callback_receives_results_once_after_filtering(
    runner_artifact: RunArtifact,
    output_format: str,
    baseline_enabled: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Custom consumers receive a snapshot of filtered results even on clean stdout."""
    received: list[tuple[str, tuple[str, ...], int, int | None]] = []

    def collect(result: ToolResult) -> None:
        received.append(
            (
                result.name,
                tuple(issue.message for issue in result.issues or []),
                result.issues_count,
                result.baselined_count,
            ),
        )

    baseline = runner_artifact.workspace_root / "baseline.json"
    exit_code = _run(
        output_format=output_format,
        baseline=baseline if baseline_enabled else None,
        on_tool_result=collect,
    )

    expected_messages = (
        ("New undefined value",)
        if baseline_enabled
        else ("Unused value 42", "New undefined value")
    )
    assert_that(received).is_equal_to(
        [
            (
                "ruff",
                expected_messages,
                len(expected_messages),
                1 if baseline_enabled else None,
            ),
        ],
    )
    assert_that(exit_code).is_equal_to(1)
    assert_that(capsys.readouterr().out).is_empty()


@pytest.mark.parametrize("output_format", ["grid", "json"])
def test_default_display_respects_clean_stdout_after_filtering(
    runner_artifact: RunArtifact,
    output_format: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default rendering shows filtered findings only when console output is allowed."""
    exit_code = _run(
        output_format=output_format,
        baseline=runner_artifact.workspace_root / "baseline.json",
    )

    output = capsys.readouterr().out
    if output_format == "json":
        assert_that(output).is_empty()
    else:
        assert_that(output).contains("New undefined value")
        assert_that(output).does_not_contain("Unused value 42")
    assert_that(runner_artifact.tool_results[0].baselined_count).is_equal_to(1)
    assert_that(exit_code).is_equal_to(1)
