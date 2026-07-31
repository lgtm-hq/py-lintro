"""Tests for the execute/render split introduced by issue #1823.

``execute_run`` is asserted on purely through the artifact it returns — it must
print nothing and write nothing. ``render_run`` is driven separately from a
fixture artifact, once per output format.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.run_renderer import render_run
from lintro.utils.execution.tool_configuration import SkippedTool, ToolsToRunResult
from lintro.utils.health_score import health_score_for_results
from lintro.utils.tool_executor import execute_run


class _FakeTool:
    """Tool double that reports a fixed number of check-mode issues."""

    def __init__(self, *, issues_count: int) -> None:
        """Store the issue count this double reports.

        Args:
            issues_count: Number of issues every ``check`` call reports.
        """
        self._issues_count = issues_count

    def set_options(self, **_kwargs: Any) -> None:
        """Accept and ignore runtime options."""
        return None

    def reset_options(self) -> None:
        """Accept and ignore option resets."""
        return None

    def check(self, _paths: Any, _options: Any) -> ToolResult:
        """Return the canned check result.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            ToolResult: The canned result for this double.
        """
        return ToolResult(
            name="ruff",
            success=self._issues_count == 0,
            issues_count=self._issues_count,
            issues=[],
        )


class _FakeOutputManager:
    """Output manager double recording what the render phase asked it to do."""

    def __init__(self, run_dir: Path) -> None:
        """Record the run directory this double reports.

        Args:
            run_dir: Directory the render phase would write into.
        """
        self.run_dir = run_dir
        self.reports_written = 0
        self.console_logs: list[str] = []
        self.cleanups = 0

    def write_console_log(self, *, content: str) -> None:
        """Record a console-log write.

        Args:
            content: Buffered console text.
        """
        self.console_logs.append(content)

    def write_reports_from_results(
        self,
        _results: list[ToolResult],
        *,
        console_text: str | None = None,
    ) -> None:
        """Record a report write.

        Args:
            _results: Ignored results.
            console_text: Ignored console text.
        """
        del console_text
        self.reports_written += 1

    def cleanup_old_runs(self) -> None:
        """Record a cleanup request."""
        self.cleanups += 1


def _context(
    *,
    tmp_path: Path,
    fake_logger: Any,
    output_format: str = "grid",
    score_only: bool = False,
) -> RunContext:
    """Build a run context wired to test doubles.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        output_format: Output format the run was asked for.
        score_only: Whether stdout carries only the numeric score.

    Returns:
        RunContext: A context safe to hand to either phase.
    """
    from lintro.config.config_loader import get_config

    return RunContext(
        action=Action.CHECK,
        selection_action=Action.CHECK,
        dry_run_preview=False,
        output_manager=_FakeOutputManager(tmp_path),
        logger=fake_logger,
        lintro_config=get_config(),
        clean_stdout_output=output_format in ("json", "sarif", "csv", "markdown"),
        score_only=score_only,
    )


@pytest.fixture
def executor_doubles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize tool discovery, configuration, and post-checks.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        te,
        "configure_tool_for_execution",
        lambda *, tool, **_kwargs: tool,
    )
    monkeypatch.setattr(
        te,
        "execute_post_checks",
        lambda **kwargs: (
            kwargs["total_issues"],
            kwargs["total_fixed"],
            kwargs["total_remaining"],
        ),
    )
    monkeypatch.setattr(te, "load_post_checks_config", lambda: {"enabled": False})


def _run_execute(
    *,
    ctx: RunContext,
    on_tool_result: Any = None,
    **overrides: Any,
) -> RunArtifact:
    """Call ``execute_run`` with the shared fixture arguments.

    Args:
        ctx: The run context to execute against.
        on_tool_result: Optional per-result display callback.
        **overrides: Extra keyword arguments forwarded to ``execute_run``.

    Returns:
        RunArtifact: The artifact the execute phase produced.
    """
    kwargs: dict[str, Any] = {
        "ctx": ctx,
        "paths": ["."],
        "tools": "ruff",
        "tool_options": None,
        "exclude": None,
        "include_venv": False,
        "group_by": "file",
        "output_format": "grid",
        "verbose": False,
        "on_tool_result": on_tool_result,
    }
    kwargs.update(overrides)
    return execute_run(**kwargs)


def test_execute_run_returns_an_artifact_and_emits_no_document(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The execute phase reports its results only through the artifact.

    Run with a clean-stdout format so the decorative pre-execution summary is
    suppressed: whatever is left on stdout would have to come from rendering,
    and the execute phase renders nothing.
    """
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff"]),
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: _FakeTool(issues_count=2),
    )
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")

    artifact = _run_execute(ctx=ctx, output_format="json")

    assert_that(artifact.tool_results).is_length(1)
    assert_that(artifact.tool_results[0].name).is_equal_to("ruff")
    assert_that(artifact.total_issues).is_equal_to(2)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.action).is_equal_to(Action.CHECK)
    assert_that(artifact.workspace_root).is_equal_to(Path.cwd())
    assert_that(artifact.health).is_not_none()
    assert_that(artifact.early_exit).is_false()
    assert_that(capsys.readouterr().out).is_empty()


def test_execute_run_streams_results_through_the_callback(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Per-tool display happens through the injected callback, not the phase."""
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff"]),
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: _FakeTool(issues_count=0),
    )
    seen: list[str] = []
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    _run_execute(ctx=ctx, on_tool_result=lambda result: seen.append(result.name))

    assert_that(seen).is_equal_to(["ruff"])


def test_execute_run_marks_an_unknown_tool_selection_as_early_exit(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A rejected tool selection produces a failing, early-exit artifact."""

    def _boom(tools: Any, action: Any, **_kwargs: Any) -> ToolsToRunResult:
        raise ValueError("Unknown tool: nope")

    monkeypatch.setattr(te, "get_tools_to_run", _boom)
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    artifact = _run_execute(ctx=ctx, tools="nope")

    assert_that(artifact.early_exit).is_true()
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.tool_results).is_empty()


def test_execute_run_records_skipped_tools_in_the_artifact(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Skipped tools appear as results so renderers can list them."""
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(
            to_run=[],
            skipped=[SkippedTool(name="hadolint", reason="not found on PATH")],
        ),
    )
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")

    artifact = _run_execute(ctx=ctx, output_format="json")

    assert_that(artifact.tool_results).is_length(1)
    assert_that(artifact.tool_results[0].skipped).is_true()
    assert_that(artifact.tool_results[0].name).is_equal_to("hadolint")
    assert_that(artifact.exit_code).is_equal_to(0)


def _artifact_fixture() -> RunArtifact:
    """Build a completed artifact standing in for a real check run.

    Returns:
        RunArtifact: A one-tool artifact with two outstanding issues.
    """
    results = [
        ToolResult(name="ruff", success=False, issues_count=2, issues=[]),
    ]
    return RunArtifact(
        tool_results=results,
        action=Action.CHECK,
        workspace_root=Path.cwd(),
        health=health_score_for_results(results),
        total_issues=2,
        total_fixed=0,
        total_remaining=2,
        exit_code=1,
    )


def test_render_run_emits_json(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The json format writes one parseable document to stdout."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")

    render_run(_artifact_fixture(), ctx=ctx, output_format="json")

    payload = json.loads(capsys.readouterr().out)
    assert_that(payload["summary"]["total_issues"]).is_equal_to(2)
    assert_that(payload["results"][0]["tool"]).is_equal_to("ruff")


def test_render_run_emits_sarif(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sarif format writes a SARIF log to stdout."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="sarif")

    render_run(_artifact_fixture(), ctx=ctx, output_format="sarif")

    payload = json.loads(capsys.readouterr().out)
    assert_that(payload["version"]).is_equal_to("2.1.0")
    assert_that(payload["runs"]).is_not_empty()


def test_render_run_emits_csv(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The csv format writes a document ``csv.reader`` can parse."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="csv")

    render_run(_artifact_fixture(), ctx=ctx, output_format="csv")

    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert_that(rows).is_not_empty()


def test_render_run_emits_the_console_summary(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """The default grid format goes through the console logger."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    render_run(_artifact_fixture(), ctx=ctx, output_format="grid")

    calls = [name for name, _args, _kwargs in fake_logger.calls]
    assert_that(calls).contains("print_execution_summary")


def test_render_run_emits_only_the_score_when_asked(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Score-only mode prints a bare number and nothing else."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, score_only=True)
    artifact = _artifact_fixture()

    render_run(artifact, ctx=ctx, output_format="grid")

    assert_that(capsys.readouterr().out.strip()).is_equal_to(str(artifact.health_score))


def test_render_run_writes_the_run_reports(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Report writing and run cleanup belong to the render phase."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    render_run(_artifact_fixture(), ctx=ctx, output_format="grid")

    assert_that(ctx.output_manager.reports_written).is_equal_to(1)
    assert_that(ctx.output_manager.cleanups).is_equal_to(1)


def test_render_run_is_a_no_op_for_an_early_exit(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that never executed renders nothing at all."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    render_run(
        RunArtifact(action=Action.CHECK, exit_code=1, early_exit=True),
        ctx=ctx,
        output_format="grid",
    )

    assert_that(capsys.readouterr().out).is_empty()
    assert_that(ctx.output_manager.reports_written).is_equal_to(0)
