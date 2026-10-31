"""Crash reporting for formatters whose claims provide no CHECK capability."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools import tool_manager
from lintro.utils.execution import parallel_executor
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.json_output import create_json_output
from tests.unit.conftest import FakeLogger


def _run_formatters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_logger: FakeLogger,
    parallel: bool,
    failure_stage: str,
    remaining: int,
) -> RunArtifact:
    """Execute CHECK-less doubles through the real execution and verify phases.

    Args:
        monkeypatch: Fixture isolating configuration and tool boundaries.
        tmp_path: Workspace containing a formatter candidate.
        fake_logger: Console logger double.
        parallel: Whether to use the parallel dispatcher.
        failure_stage: Whether configuration or the fix call crashes.
        remaining: Measured residual reported by the surviving formatter.

    Returns:
        The completed format-run artifact.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.js").write_text("let x=1;\n", encoding="utf-8")
    tools: dict[str, MagicMock] = {}
    for name in ("crashed", "measured"):
        tools[name] = MagicMock(
            definition=SimpleNamespace(
                name=name,
                claims=[Claim(patterns=["*.js"], capabilities={Cap.FORMAT})],
            ),
        )
        tools[name].check.side_effect = AssertionError("CHECK was not declared")
    tools["crashed"].fix.side_effect = RuntimeError("formatter crashed")
    tools["measured"].fix.return_value = ToolResult(
        name="measured",
        success=remaining == 0,
        output="measured formatter output",
        issues_count=remaining,
        issues=[
            BaseIssue(file="target.js", line=index + 1, message="still unformatted")
            for index in range(remaining)
        ],
        initial_issues_count=3,
        fixed_issues_count=3 - remaining,
        remaining_issues_count=remaining,
    )

    def configure(*, tool: Any, tool_name: str, **_kwargs: Any) -> Any:
        if tool_name == "crashed" and failure_stage == "configure":
            raise RuntimeError("formatter configuration failed")
        return tool

    monkeypatch.setattr(tool_manager, "get_tool", tools.__getitem__)
    monkeypatch.setattr(
        tool_manager,
        "get_parallel_batches",
        lambda names, **_kwargs: [list(names)],
    )
    monkeypatch.setattr(te, "configure_tool_for_execution", configure)
    monkeypatch.setattr(parallel_executor, "configure_tool_for_execution", configure)
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda *_args, **_kwargs: ToolsToRunResult(to_run=list(tools)),
    )
    config = LintroConfig()
    config.execution.parallel = parallel
    config.execution.max_workers = 2
    config.execution.max_fix_retries = 1
    config.execution.auto_install_deps = False
    ctx = RunContext(
        action=Action.FIX,
        selection_action=Action.FIX,
        dry_run_preview=False,
        output_manager=SimpleNamespace(run_dir=tmp_path, base_dir=tmp_path),
        logger=fake_logger,
        lintro_config=config,
        clean_stdout_output=True,
    )
    return te.execute_run(
        ctx=ctx,
        paths=[str(tmp_path)],
        tools="crashed,measured",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        run_gates=False,
    )


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
@pytest.mark.parametrize("failure_stage", ["configure", "fix"])
@pytest.mark.parametrize("remaining", [0, 2], ids=["clean-peer", "issues-peer"])
def test_crashed_formatter_reports_unknown_and_preserves_measured_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_logger: FakeLogger,
    parallel: bool,
    failure_stage: str,
    remaining: int,
) -> None:
    """A crash cannot become a measured zero when the verify pass has no CHECK.

    Args:
        monkeypatch: Fixture isolating configuration and tool boundaries.
        tmp_path: Temporary formatter workspace.
        fake_logger: Console logger double.
        parallel: Whether to dispatch concurrently.
        failure_stage: Which boundary raises the runtime error.
        remaining: Surviving formatter's measured after-count.
    """
    artifact = _run_formatters(
        monkeypatch,
        tmp_path,
        fake_logger,
        parallel,
        failure_stage,
        remaining,
    )
    data = create_json_output(
        action=artifact.action,
        results=artifact.tool_results,
        total_issues=artifact.total_issues,
        total_fixed=artifact.total_fixed,
        total_remaining=artifact.total_remaining,
        exit_code=artifact.exit_code,
    )
    by_name = {result.name: result for result in artifact.tool_results}
    rendered = {result["tool"]: result for result in data["results"]}

    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(by_name["crashed"].success).is_false()
    assert_that(by_name["crashed"].residual_unknown).is_true()
    assert_that(by_name["crashed"].residual_unknown_reason).is_equal_to("tool crashed")
    assert_that(by_name["crashed"].fixed_issues_count).is_none()
    assert_that(by_name["crashed"].remaining_issues_count).is_none()
    assert_that(rendered["crashed"]["remaining"]).is_none()
    assert_that(rendered["crashed"]["net_resolved"]).is_none()
    assert_that(rendered["crashed"]["fixed"]).is_none()
    assert_that(rendered["crashed"]["residual_unknown_reason"]).is_equal_to(
        "tool crashed",
    )
    assert_that(rendered["crashed"]["output"]).contains("formatter")
    assert_that(rendered["measured"]["output"]).is_equal_to("measured formatter output")
    assert_that(rendered["measured"]["remaining"]).is_equal_to(remaining)
    assert_that(rendered["measured"]["net_resolved"]).is_equal_to(3 - remaining)
    assert_that(rendered["measured"]["fixed"]).is_equal_to(3 - remaining)
    assert_that(rendered["measured"]).does_not_contain_key("residual_unknown")
    assert_that(data["summary"]["total_remaining"]).is_equal_to(remaining)
    assert_that(data["summary"]["total_net_resolved"]).is_equal_to(3 - remaining)
    assert_that(data["summary"]["total_fixed"]).is_equal_to(3 - remaining)
    assert_that(data["summary"]["residual_unknown_tools"]).is_equal_to(["crashed"])
