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
from typing import Any, Never
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.severity_counts import SeverityCounts
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools import tool_manager
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.run_renderer import render_run
from lintro.utils.execution.tool_configuration import SkippedTool, ToolsToRunResult
from lintro.utils.severity_baseline import (
    read_severity_baseline,
    write_severity_baseline,
)
from lintro.utils.severity_counts import count_severities
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
        # Parsed issues, not a bare count: ``count_severities`` reads
        # severities off this list, so a fixture with an empty one would make
        # the execute phase's tally unobservable.
        issues: list[Any] = [
            RuffIssue(file="a.py", line=index + 1, code="F401", message="unused")
            for index in range(self._issues_count)
        ]
        return ToolResult(
            name="ruff",
            success=self._issues_count == 0,
            issues_count=self._issues_count,
            issues=issues,
        )


class _FakeOutputManager:
    """Output manager double recording what the render phase asked it to do."""

    def __init__(self, run_dir: Path) -> None:
        """Record the run directory this double reports.

        Args:
            run_dir: Temporary directory to build under. A ``logs`` log root
                and a pruneable ``logs/run-test`` child are created inside it,
                mirroring production, so a baseline written into the run
                directory instead of the log root fails the placement test.
        """
        self.base_dir = run_dir / "logs"
        self.run_dir = self.base_dir / "run-test"
        self.run_dir.mkdir(parents=True, exist_ok=True)
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
    group_by: str = "auto",
    profile: bool = False,
) -> RunContext:
    """Build a run context wired to test doubles.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        output_format: Output format the run was asked for.
        group_by: How issues should be grouped in formatted output.
        profile: Whether to emit the per-tool performance profile.

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
        group_by=group_by,
        profile=profile,
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
    # A real tally, not a presence check: ``severity_counts`` is a non-Optional
    # field, so ``is_not_none()`` could never fail. ``RuffIssue`` maps F401 to
    # WARNING, so the execute phase must report two warnings here.
    assert_that(artifact.severity_counts).is_equal_to(SeverityCounts(warnings=2))
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


def _console_texts(fake_logger: Any) -> str:
    """Join all console_output text passed to the fake logger.

    Args:
        fake_logger: FakeLogger instance whose calls were recorded.

    Returns:
        A single string of all console_output text arguments.
    """
    parts: list[str] = []
    for name, args, kwargs in fake_logger.calls:
        if name != "console_output":
            continue
        if "text" in kwargs:
            parts.append(str(kwargs["text"]))
        elif args:
            parts.append(str(args[0]))
    return "\n".join(parts)


def test_execute_run_prints_detection_notice_on_human_output(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A language-scoped run prints the no-config notice on human output.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        executor_doubles: Neutralized executor collaborators.
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(
            to_run=["ruff"],
            detected_languages=["python"],
            scoped_by_detection=True,
        ),
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: _FakeTool(issues_count=0),
    )
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    _run_execute(ctx=ctx, tools=None)

    assert_that(_console_texts(fake_logger)).contains("No config found")
    assert_that(_console_texts(fake_logger)).contains("lintro init")


def test_execute_run_hides_detection_notice_on_machine_output(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Machine-readable stdout suppresses the language-scope notice.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        executor_doubles: Neutralized executor collaborators.
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(
            to_run=["ruff"],
            detected_languages=["python"],
            scoped_by_detection=True,
        ),
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: _FakeTool(issues_count=0),
    )
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="json",
    )

    _run_execute(ctx=ctx, tools=None, output_format="json")

    assert_that(_console_texts(fake_logger)).does_not_contain("No config found")


def test_execute_run_forwards_paths_as_scan_roots(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Default chk/fmt runs pass scan paths into language detection.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        executor_doubles: Neutralized executor collaborators.
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    captured: dict[str, object] = {}

    def fake_get_tools(
        tools: str | None,
        action: object,
        **kwargs: object,
    ) -> ToolsToRunResult:
        captured["tools"] = tools
        captured["scan_roots"] = kwargs.get("scan_roots")
        return ToolsToRunResult(to_run=["ruff"])

    monkeypatch.setattr(te, "get_tools_to_run", fake_get_tools)
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: _FakeTool(issues_count=0),
    )
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    _run_execute(ctx=ctx, tools=None, paths=["src/app.py"])

    assert_that(captured["tools"]).is_none()
    assert_that(captured["scan_roots"]).is_equal_to(["src/app.py"])


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
        severity_counts=count_severities(results),
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


def test_render_run_enriches_categories_before_json_stdout(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--group-by category`` writes canonical labels onto issues before JSON."""
    issue = RuffIssue(file="a.py", line=1, code="S105", message="hardcoded")
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[issue],
        ),
    ]
    artifact = RunArtifact(
        tool_results=results,
        action=Action.CHECK,
        workspace_root=Path.cwd(),
        severity_counts=count_severities(results),
        total_issues=1,
        total_fixed=0,
        total_remaining=1,
        exit_code=1,
    )
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="json",
        group_by="category",
    )

    render_run(artifact, ctx=ctx, output_format="json")

    payload = json.loads(capsys.readouterr().out)
    assert_that(issue.category).is_equal_to("Security")
    assert_that(payload["results"][0]["issues"][0]["category"]).is_equal_to(
        "Security",
    )


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


def test_render_run_prints_the_severity_counts(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A check run always reports what it found, by severity.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2, warnings=1)

    render_run(artifact, ctx=ctx, output_format="grid")

    assert_that(_console_texts(fake_logger)).contains(
        "Issues: 2 errors, 1 warning, 0 info",
    )


def test_render_run_prints_no_delta_without_a_baseline(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A first run in a workspace has nothing to compare against.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    render_run(_artifact_fixture(), ctx=ctx, output_format="grid")

    assert_that(_console_texts(fake_logger)).does_not_contain("Change since last run")


@pytest.mark.parametrize(
    ("previous", "expected_line", "expected_color"),
    [
        (
            SeverityCounts(errors=14),
            "Change since last run: -12 errors",
            "green",
        ),
        (
            SeverityCounts(errors=1),
            "Change since last run: +1 error",
            "red",
        ),
        (
            SeverityCounts(errors=2),
            "Change since last run: no change",
            "cyan",
        ),
        (
            SeverityCounts(errors=2, warnings=6, info=1),
            "Change since last run: -6 warnings, -1 info",
            "green",
        ),
    ],
    ids=["improved", "regressed", "unchanged", "non-error-severities"],
)
def test_render_run_colors_the_delta_by_direction_of_improvement(
    tmp_path: Path,
    fake_logger: Any,
    previous: SeverityCounts,
    expected_line: str,
    expected_color: str,
) -> None:
    """Fewer issues is green even though the arithmetic sign is negative.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        previous: Counts recorded for the preceding run.
        expected_line: Delta line the renderer must print.
        expected_color: Colour that line must be printed in.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2)
    artifact.previous_severity_counts = previous

    render_run(artifact, ctx=ctx, output_format="grid")

    colors = {
        str(kwargs.get("text")): kwargs.get("color")
        for name, _args, kwargs in fake_logger.calls
        if name == "console_output"
    }
    assert_that(colors).contains_key(expected_line)
    assert_that(colors[expected_line]).is_equal_to(expected_color)


def test_render_run_records_the_baseline_for_the_next_run(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A check run leaves its counts behind for the next run's delta.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2, info=1)

    render_run(artifact, ctx=ctx, output_format="grid")

    assert_that(read_severity_baseline(ctx.output_manager.base_dir)).is_equal_to(
        SeverityCounts(errors=2, info=1),
    )


def _unmeasured_artifacts() -> list[tuple[str, RunArtifact]]:
    """Build the run shapes that must never overwrite a real baseline.

    Each one reports zero issues for a reason other than "the project is
    clean", so recording it would make the next real check report every
    existing issue as newly introduced.

    Returns:
        list[tuple[str, RunArtifact]]: ``(id, artifact)`` pairs.
    """
    skipped_only = RunArtifact(
        action=Action.CHECK,
        tool_results=[
            ToolResult(
                name="hadolint",
                success=True,
                skipped=True,
                skip_reason="hadolint not found",
            ),
        ],
    )
    no_files = RunArtifact(
        action=Action.CHECK,
        tool_results=[
            ToolResult(
                name="ruff",
                success=True,
                skipped=False,
                output="No .py/.pyi files found to check.",
            ),
        ],
    )
    dry_run = RunArtifact(
        action=Action.CHECK,
        dry_run_preview=True,
        tool_results=[ToolResult(name="ruff", success=True, issues_count=0)],
    )
    early = RunArtifact(action=Action.CHECK, early_exit=True)
    timed_out = RunArtifact(
        action=Action.CHECK,
        tool_results=[
            ToolResult(
                name="semgrep",
                success=False,
                skipped=False,
                timed_out=True,
                output="timed out after 300s",
            ),
        ],
    )
    real = [ToolResult(name="ruff", success=True, output="All checks passed")]
    return [
        ("empty", RunArtifact(action=Action.CHECK)),
        ("all-skipped", skipped_only),
        ("no-files-matched", no_files),
        ("all-timed-out", timed_out),
        ("dry-run-preview", dry_run),
        ("early-exit", early),
        ("fmt", RunArtifact(action=Action.FIX, tool_results=list(real))),
        ("test", RunArtifact(action=Action.TEST, tool_results=list(real))),
    ]


@pytest.mark.parametrize(
    "artifact",
    [pair[1] for pair in _unmeasured_artifacts()],
    ids=[pair[0] for pair in _unmeasured_artifacts()],
)
def test_render_run_does_not_record_a_baseline_for_an_unmeasured_run(
    tmp_path: Path,
    fake_logger: Any,
    artifact: RunArtifact,
) -> None:
    """Only a run that actually measured the project may replace the baseline.

    An all-skipped run still carries ``skipped=True`` placeholder results, so
    a non-empty result list is not evidence that anything was measured; the
    same holds for a toolset that matched no files and for a ``fmt --dry-run``
    preview, whose counts are the auto-fixable subset only.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        artifact: A run shape that measured nothing comparable.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)
    write_severity_baseline(ctx.output_manager.base_dir, SeverityCounts(errors=9))

    render_run(artifact, ctx=ctx, output_format="grid")

    assert_that(read_severity_baseline(ctx.output_manager.base_dir)).is_equal_to(
        SeverityCounts(errors=9),
    )


def test_render_run_does_not_record_a_baseline_for_fmt(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Only ``check`` records a baseline; ``fmt`` measures something else.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)
    artifact = _artifact_fixture()
    artifact.action = Action.FIX

    render_run(artifact, ctx=ctx, output_format="grid")

    assert_that(read_severity_baseline(ctx.output_manager.base_dir)).is_none()


def test_render_run_json_carries_the_counts_and_delta(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON output publishes the tallies additively under ``summary``.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        capsys: Pytest stdout capture fixture.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2)
    artifact.previous_severity_counts = SeverityCounts(errors=14)

    render_run(artifact, ctx=ctx, output_format="json")

    summary = json.loads(capsys.readouterr().out)["summary"]
    assert_that(summary["severity_counts"]).is_equal_to(
        {"error": 2, "warning": 0, "info": 0, "total": 2},
    )
    assert_that(summary["severity_delta"]).is_equal_to(
        {"error": -12, "warning": 0, "info": 0, "total": -12},
    )
    assert_that(summary["total_issues"]).is_equal_to(2)


def test_render_run_output_file_json_carries_the_counts_and_delta(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An ``--output`` JSON file carries the same tallies as stdout.

    A consumer reading the file must not have to fall back to parsing the
    console to learn what the run found.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        capsys: Pytest stdout capture fixture.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2)
    artifact.previous_severity_counts = SeverityCounts(errors=14)
    output_file = tmp_path / "report.json"

    render_run(
        artifact,
        ctx=ctx,
        output_format="json",
        output_file=str(output_file),
    )
    capsys.readouterr()

    summary = json.loads(output_file.read_text(encoding="utf-8"))["summary"]
    assert_that(summary["severity_counts"]).is_equal_to(
        {"error": 2, "warning": 0, "info": 0, "total": 2},
    )
    assert_that(summary["severity_delta"]).is_equal_to(
        {"error": -12, "warning": 0, "info": 0, "total": -12},
    )


def test_render_run_json_artifact_carries_the_counts_and_delta(
    tmp_path: Path,
    fake_logger: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A configured JSON artifact carries the tallies too.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest stdout capture fixture.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(warnings=5)
    artifact.previous_severity_counts = SeverityCounts(warnings=1)

    # ``get_config`` caches a process-wide config whose ExecutionConfig is
    # mutable, so this must be restored or it leaks into every later test.
    original_artifacts = list(ctx.lintro_config.execution.artifacts)
    ctx.lintro_config.execution.artifacts = ["json"]
    try:
        render_run(artifact, ctx=ctx, output_format="json")
    finally:
        ctx.lintro_config.execution.artifacts = original_artifacts
    capsys.readouterr()

    written = Path(".lintro") / "artifacts" / "json" / "results.json"
    summary = json.loads(written.read_text(encoding="utf-8"))["summary"]
    assert_that(summary["severity_counts"]).is_equal_to(
        {"error": 0, "warning": 5, "info": 0, "total": 5},
    )
    assert_that(summary["severity_delta"]).is_equal_to(
        {"error": 0, "warning": 4, "info": 0, "total": 4},
    )


def test_render_run_json_omits_the_delta_without_a_baseline(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A first run publishes counts but no delta key at all.

    An absent key is unambiguous; a zero delta would read as "nothing changed"
    for a run that had nothing to compare against.

    Args:
        tmp_path: Temporary directory standing in for the run directory.
        fake_logger: Console logger double.
        capsys: Pytest stdout capture fixture.
    """
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, output_format="json")
    artifact = _artifact_fixture()
    artifact.severity_counts = SeverityCounts(errors=2)
    artifact.previous_severity_counts = None

    render_run(artifact, ctx=ctx, output_format="json")

    summary = json.loads(capsys.readouterr().out)["summary"]
    assert_that(summary).contains_key("severity_counts")
    assert_that(summary).does_not_contain_key("severity_delta")


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


def test_simple_runner_finalizes_output_after_execution_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
) -> None:
    """The convenience runner should release its active marker on exceptions.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        fake_logger: Console logger double recording every call.
    """
    finalization: list[str] = []

    class _FailingOutputManager:
        """Output manager double whose cleanup step raises."""

        def mark_run_complete(self) -> None:
            """Record that the active-run marker was released."""
            finalization.append("mark_run_complete")

        def cleanup_old_runs(self) -> None:
            """Record the cleanup attempt and fail it.

            Raises:
                OSError: Always, standing in for an unwritable run directory.
            """
            finalization.append("cleanup_old_runs")
            raise OSError("permission denied")

    ctx = MagicMock(
        output_manager=_FailingOutputManager(),
        logger=fake_logger,
        action=Action.CHECK,
    )
    monkeypatch.setattr(te, "build_run_context", lambda **_kwargs: ctx)
    monkeypatch.setattr(
        "lintro.utils.execution.run_renderer.make_result_display",
        lambda **_kwargs: None,
    )

    def _fail_execute(**_kwargs: object) -> RunArtifact:
        raise RuntimeError("execution failed")

    monkeypatch.setattr(te, "execute_run", _fail_execute)

    with pytest.raises(RuntimeError, match="execution failed"):
        te.run_lint_tools_simple(
            action=Action.CHECK,
            paths=["."],
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="grid",
            verbose=False,
        )

    assert_that(finalization).is_equal_to(["mark_run_complete", "cleanup_old_runs"])
    warnings = [name for name, _args, _kwargs in fake_logger.calls if name == "warning"]
    assert_that(warnings).is_length(1)


def _profiled_artifact() -> RunArtifact:
    """Build a completed artifact with recorded per-tool duration.

    Returns:
        RunArtifact: A one-tool artifact whose result can appear in ``--profile``.
    """
    issue = RuffIssue(file="a.py", line=1, code="F401", message="unused")
    results = [
        ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[issue],
            duration_seconds=1.25,
        ),
    ]
    return RunArtifact(
        tool_results=results,
        action=Action.CHECK,
        workspace_root=Path.cwd(),
        severity_counts=count_severities(results),
        total_issues=1,
        total_fixed=0,
        total_remaining=1,
        exit_code=1,
    )


def _console_text(fake_logger: Any) -> str:
    """Join recorded ``console_output`` calls into one string.

    Args:
        fake_logger: Logger double capturing method calls.

    Returns:
        Concatenated console text.
    """
    parts: list[str] = []
    for name, args, kwargs in fake_logger.calls:
        if name != "console_output":
            continue
        if "text" in kwargs:
            parts.append(str(kwargs["text"]))
        elif args:
            parts.append(str(args[0]))
    return "\n".join(parts)


def test_execute_run_records_duration_on_tool_crash(
    monkeypatch: pytest.MonkeyPatch,
    executor_doubles: None,
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """A sequential crash still records elapsed time on the synthetic result."""
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff"]),
    )

    def _boom(_name: str) -> Never:
        raise RuntimeError("ruff exploded")

    monkeypatch.setattr(tool_manager, "get_tool", _boom)
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger)

    artifact = _run_execute(ctx=ctx)

    assert_that(artifact.tool_results).is_length(1)
    result = artifact.tool_results[0]
    assert_that(result.name).is_equal_to("ruff")
    assert_that(result.success).is_false()
    assert_that(result.duration_seconds).is_not_none()
    assert_that(result.duration_seconds).is_greater_than_or_equal_to(0.0)


def test_render_run_grid_profile_prints_cumulative(
    tmp_path: Path,
    fake_logger: Any,
) -> None:
    """Grid ``--profile`` prints the timing table including the CUMULATIVE row."""
    ctx = _context(tmp_path=tmp_path, fake_logger=fake_logger, profile=True)

    render_run(_profiled_artifact(), ctx=ctx, output_format="grid")

    assert_that(_console_text(fake_logger)).contains("CUMULATIVE")


def test_render_run_json_stdout_includes_profile(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON stdout attaches the profile payload when ``ctx.profile`` is on."""
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="json",
        profile=True,
    )

    render_run(_profiled_artifact(), ctx=ctx, output_format="json")

    payload = json.loads(capsys.readouterr().out)
    assert_that(payload).contains_key("profile")
    assert_that(payload["profile"]).contains_key(
        "cumulative_tool_duration",
        "tools",
        "suggestions",
    )
    assert_that(payload["profile"]["tools"][0]["name"]).is_equal_to("ruff")
    assert_that(payload["profile"]["tools"][0]).contains_key("files_with_issues")
    assert_that(payload["profile"]["tools"][0]).does_not_contain_key("files_checked")


def test_render_run_json_output_file_includes_profile(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON ``--output`` files carry the same profile payload as stdout."""
    output_path = tmp_path / "out.json"
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="json",
        profile=True,
    )

    render_run(
        _profiled_artifact(),
        ctx=ctx,
        output_format="json",
        output_file=str(output_path),
    )

    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(output_path.read_text())
    assert_that(stdout_payload).contains_key("profile")
    assert_that(file_payload).contains_key("profile")
    assert_that(file_payload["profile"]["tools"][0]["files_with_issues"]).is_equal_to(
        1,
    )


def test_render_run_json_artifact_includes_profile(
    tmp_path: Path,
    fake_logger: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Configured JSON artifacts receive the profile payload on a profiled run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="grid",
        profile=True,
    )
    original_artifacts = list(ctx.lintro_config.execution.artifacts)
    ctx.lintro_config.execution.artifacts = ["json"]
    try:
        render_run(_profiled_artifact(), ctx=ctx, output_format="grid")
    finally:
        ctx.lintro_config.execution.artifacts = original_artifacts

    artifact_path = tmp_path / ".lintro" / "artifacts" / "json" / "results.json"
    payload = json.loads(artifact_path.read_text())
    assert_that(payload).contains_key("profile")
    assert_that(payload["profile"]["tools"][0]["name"]).is_equal_to("ruff")


def test_render_run_csv_stdout_stays_profile_free(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CSV stdout stays a CSV document even when ``--profile`` is on."""
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="csv",
        profile=True,
    )

    render_run(_profiled_artifact(), ctx=ctx, output_format="csv")

    out = capsys.readouterr().out
    assert_that(out).does_not_contain("CUMULATIVE")
    assert_that(out).does_not_contain('"profile"')
    rows = list(csv.reader(io.StringIO(out)))
    assert_that(rows).is_not_empty()


def test_render_run_sarif_stdout_stays_profile_free(
    tmp_path: Path,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SARIF stdout does not grow a ``profile`` key when ``--profile`` is on."""
    ctx = _context(
        tmp_path=tmp_path,
        fake_logger=fake_logger,
        output_format="sarif",
        profile=True,
    )

    render_run(_profiled_artifact(), ctx=ctx, output_format="sarif")

    payload = json.loads(capsys.readouterr().out)
    assert_that(payload).does_not_contain_key("profile")
    assert_that(payload["version"]).is_equal_to("2.1.0")
