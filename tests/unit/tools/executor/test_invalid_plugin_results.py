"""Invalid mutable plugin results fail per tool before output consumers run."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_outcome import Checked, FixUnmeasured
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import ToolRegistry
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.json_output import create_json_output
from lintro.utils.streaming_output import StreamingResultHandler


@dataclass
class _ResultPlugin(BaseToolPlugin):
    """Registered plugin returning a legacy result without outcome validation."""

    result: ToolResult

    @property
    def definition(self) -> ToolDefinition:
        """Declare a formatter with no verifying CHECK capability.

        Returns:
            Metadata for execution, scheduling and verification.
        """
        return ToolDefinition(
            name=self.result.name,
            description="Legacy result fixture",
            can_fix=True,
            file_patterns=["*.py"],
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FORMAT})],
        )

    def check(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Return independent mutable state, as an external plugin could.

        Args:
            paths: Unused scan targets.
            options: Unused runtime options.

        Returns:
            A copy retaining any invalid legacy fields.
        """
        return deepcopy(self.result)

    def fix(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Return the same legacy result under format execution.

        Args:
            paths: Scan targets forwarded to the fixture check.
            options: Runtime options forwarded to the fixture check.

        Returns:
            The configured legacy result.
        """
        return self.check(paths, options)


@pytest.fixture
def run_plugins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[..., RunArtifact]:
    """Run registered plugins through real configuration and execution.

    Args:
        monkeypatch: Fixture isolating selection and working directory.
        tmp_path: Temporary workspace and output directory.

    Returns:
        Executor accepting a bad result, execution mode, action and callback.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda *_args, **_kwargs: ToolsToRunResult(to_run=["bad", "peer"]),
    )

    def run(
        bad: ToolResult,
        parallel: bool,
        action: Action,
        callback: Callable[[ToolResult], None],
    ) -> RunArtifact:
        peer = ToolResult(
            name="peer",
            success=True,
            output="peer output survives",
            initial_issues_count=2 if action == Action.FIX else None,
            fixed_issues_count=2 if action == Action.FIX else None,
            remaining_issues_count=0 if action == Action.FIX else None,
        )
        for result in (bad, peer):
            ToolRegistry.register(
                _ResultPlugin,
                instance=_ResultPlugin(result=result),
                origin="external-fixture",
            )
        config = LintroConfig()
        config.execution.parallel = parallel
        config.execution.max_workers = 2
        config.execution.max_fix_retries = 1
        config.execution.auto_install_deps = False
        ctx = RunContext(
            action=action,
            selection_action=action,
            dry_run_preview=False,
            output_manager=SimpleNamespace(run_dir=tmp_path, base_dir=tmp_path),
            logger=ThreadSafeConsoleLogger(art_enabled=False),
            lintro_config=config,
            clean_stdout_output=True,
        )
        return te.execute_run(
            ctx=ctx,
            paths=[str(tmp_path)],
            tools="bad,peer",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="auto",
            output_format="json",
            verbose=False,
            run_gates=False,
            on_tool_result=callback,
        )

    return run


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"timed_out": True, "success": True}, id="timeout-success"),
        pytest.param({"no_files": True, "success": False}, id="no-files-failure"),
        pytest.param(
            {"skipped": True, "skip_reason": "declined", "issues_count": 1},
            id="skipped-issues",
        ),
        pytest.param({"issues_count": True}, id="bool-count"),
        pytest.param({"remaining_issues_count": "N/A"}, id="legacy-sentinel"),
    ],
)
def test_invalid_plugin_result_fails_closed_without_losing_peer_output(
    run_plugins: Callable[..., RunArtifact],
    tmp_path: Path,
    parallel: bool,
    action: Action,
    fields: dict[str, Any],
) -> None:
    """Both executors report invalid legacy states safely across output consumers.

    Args:
        run_plugins: Execute registered plugins in an isolated workspace.
        tmp_path: Directory for JSONL output.
        parallel: Whether to dispatch concurrently.
        action: Check or fix execution.
        fields: Contradictory legacy fields accepted by ToolResult construction.
    """
    bad = ToolResult(name="bad", **fields)
    with pytest.raises(ValueError):
        _ = bad.outcome
    target = tmp_path / "results.jsonl"
    with StreamingResultHandler("jsonl", action, str(target)) as handler:
        artifact = run_plugins(bad, parallel, action, handler.handle_result)
    results = {result.name: result for result in artifact.tool_results}
    normalized = results["bad"]
    assert_that(normalized.outcome.execution).is_equal_to(Checked(False))
    assert_that(normalized.output).contains("ValueError")
    assert_that(normalized.duration_seconds).is_greater_than_or_equal_to(0)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(handler.get_exit_code()).is_equal_to(1)
    assert_that(handler.get_totals()).contains_entry(
        {"tools_run": 2},
        {"tools_failed": 1},
    )
    streamed = {
        row["tool"]: row for row in map(json.loads, target.read_text().splitlines())
    }
    assert_that(streamed["peer"]["output"]).is_equal_to("peer output survives")
    assert_that(streamed["bad"]["success"]).is_false()
    wire = create_json_output(
        action,
        artifact.tool_results,
        artifact.total_issues,
        artifact.total_fixed,
        artifact.total_remaining,
        artifact.exit_code,
    )
    rendered = {row["tool"]: row for row in wire["results"]}
    assert_that(rendered["peer"]["output"]).is_equal_to("peer output survives")
    assert_that(rendered["bad"]["success"]).is_false()
    console = ThreadSafeConsoleLogger(art_enabled=False)
    console.print_execution_summary(action, artifact.tool_results)
    assert_that(console.get_buffer()).contains("bad", "peer", "FAIL", "PASS")
    if action == Action.FIX:
        assert_that(normalized.outcome.accounting).is_equal_to(
            FixUnmeasured("tool crashed"),
        )
        assert_that(rendered["bad"]).contains_entry(
            {"fixed": None},
            {"remaining": None},
        )
        assert_that(streamed["bad"]).does_not_contain_key(
            "fixed_issues_count",
            "remaining_issues_count",
        )
        assert_that(console.get_buffer()).contains("unknown", "Residual unknown")
        assert_that((artifact.total_fixed, artifact.total_remaining)).is_equal_to(
            (2, 0),
        )


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
def test_result_renderer_errors_propagate_outside_plugin_boundary(
    run_plugins: Callable[..., RunArtifact],
    parallel: bool,
) -> None:
    """A reporting error must not be relabelled as a plugin's malformed outcome.

    Args:
        run_plugins: Execute registered plugins in an isolated workspace.
        parallel: Whether to dispatch concurrently.
    """

    def fail_to_render(result: ToolResult) -> None:
        raise ValueError(f"renderer failed for {result.name}")

    with pytest.raises(ValueError, match="renderer failed for bad"):
        run_plugins(
            ToolResult(name="bad", success=True),
            parallel,
            Action.CHECK,
            fail_to_render,
        )
