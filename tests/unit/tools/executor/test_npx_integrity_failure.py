"""An npx cache that fails its integrity check is an execution failure (#3406)."""

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
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution import parallel_executor
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.npx_integrity import NpxIntegrityError
from tests.unit.conftest import FakeLogger


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
def test_refused_npx_cache_is_reported_as_an_incomplete_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_logger: FakeLogger,
    parallel: bool,
) -> None:
    """The run fails with the reason, no findings, and incomplete findings.

    Args:
        monkeypatch: Fixture isolating configuration and tool boundaries.
        tmp_path: Workspace with one file to check.
        fake_logger: Console logger double.
        parallel: Whether to dispatch through the parallel executor.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "page.html").write_text("<p>x</p>\n", encoding="utf-8")
    tools: dict[str, MagicMock] = {}
    for name in ("npx-tool", "peer"):
        tools[name] = MagicMock(
            definition=SimpleNamespace(
                name=name,
                claims=[Claim(patterns=["*.html"], capabilities={Cap.CHECK})],
            ),
        )
    tools["npx-tool"].check.side_effect = NpxIntegrityError(
        "lintro's npm cache entry /c/_npx/0 node_modules/x has bin.js changed",
    )
    tools["peer"].check.return_value = ToolResult(
        name="peer",
        success=True,
        output="",
        issues_count=0,
    )

    def configure(*, tool: Any, **_kwargs: Any) -> Any:
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
    config.execution.auto_install_deps = False
    ctx = RunContext(
        action=Action.CHECK,
        selection_action=Action.CHECK,
        dry_run_preview=False,
        output_manager=SimpleNamespace(run_dir=tmp_path, base_dir=tmp_path),
        logger=fake_logger,
        lintro_config=config,
        clean_stdout_output=True,
    )

    artifact = te.execute_run(
        ctx=ctx,
        paths=[str(tmp_path)],
        tools="npx-tool,peer",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        run_gates=False,
    )
    by_name = {result.name: result for result in artifact.tool_results}

    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(by_name["npx-tool"].execution_failed).is_true()
    assert_that(by_name["npx-tool"].findings_complete).is_false()
    assert_that(by_name["npx-tool"].issues_count).is_zero()
    assert_that(by_name["npx-tool"].output).contains("bin.js changed")
    assert_that(by_name["peer"].execution_failed).is_false()
