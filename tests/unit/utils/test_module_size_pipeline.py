"""Run-level preservation and control cases for module-size read failures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools import verify_pass
from lintro.utils import gates, tool_executor
from lintro.utils.console import ThreadSafeConsoleLogger
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.run_renderer import render_run
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.output import OutputManager


@pytest.fixture
def unreadable_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Provide two unreadable modules and one readable formatting target.

    Args:
        tmp_path: Workspace for source files and reports.
        monkeypatch: Pytest patch fixture.

    Yields:
        Path: Directory whose module-size reads simulate filesystem failures.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LINTRO_LOG_DIR", str(tmp_path / "reports"))
    source = tmp_path / "source"
    source.mkdir()
    module = source / "measured.py"
    module.write_text("x=1\n", encoding="utf-8")
    for name in ("denied.py", "disappeared.py"):
        (source / name).touch()
    real_open = open

    def read_source(file: str, *args: Any, **kwargs: Any) -> Any:
        if Path(file).name == "denied.py":
            raise PermissionError(13, "permission denied", file)
        if Path(file).name == "disappeared.py":
            raise FileNotFoundError(2, "file disappeared", file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(gates, "load_module_size_config", lambda: {})
    monkeypatch.setattr(gates, "load_lintro_tool_config", lambda _name: {})
    with patch("lintro.utils.module_size.open", side_effect=read_source):
        yield source


@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize("output_format", ["plain", "json"])
@pytest.mark.parametrize("remaining", [0, 1], ids=["clean-tool", "tool-finding"])
def test_read_failures_preserve_run_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: Action,
    output_format: str,
    remaining: int,
    unreadable_source: Path,
) -> None:
    """The real gate, aggregation and render phases retain findings and fixes.

    Args:
        tmp_path: Workspace for source files and rendered reports.
        monkeypatch: Pytest patch fixture.
        capsys: Captured console documents.
        action: Check or formatting action.
        output_format: Human or machine-readable output.
        remaining: Findings left by the completed primary tool.
        unreadable_source: Directory containing readable and unreadable modules.
    """
    module = unreadable_source / "measured.py"
    issue = RuffIssue(file=str(module), line=1, code="F401", message="unused import")
    fixed = 2 if action == Action.FIX else 0
    primary = ToolResult(
        name="ruff",
        success=remaining == 0,
        output="primary findings preserved",
        issues_count=remaining,
        issues=[issue] if remaining else [],
        fixed_issues_count=fixed,
        remaining_issues_count=remaining,
        capability=Cap.FIX if action == Action.FIX else Cap.CHECK,
    )

    def complete_primary_tools(**_kwargs: Any) -> list[ToolResult]:
        if action == Action.FIX:
            module.write_text("x = 1\n", encoding="utf-8")
        return [primary]

    monkeypatch.setattr(
        tool_executor,
        "get_tools_to_run",
        lambda *_args, **_kwargs: ToolsToRunResult(to_run=["ruff"]),
    )
    monkeypatch.setattr(
        tool_executor,
        "_execute_tools_sequential",
        complete_primary_tools,
    )
    monkeypatch.setattr(tool_executor, "_run_verify_phase", lambda **_kwargs: None)
    monkeypatch.setattr(
        verify_pass,
        "capture_verify_baseline",
        lambda **_kwargs: verify_pass.VerifyBaseline(candidates=()),
    )
    config = LintroConfig()
    config.execution.artifacts = ["json"]
    ctx = RunContext(
        action=action,
        selection_action=action,
        dry_run_preview=False,
        output_manager=OutputManager(),
        logger=ThreadSafeConsoleLogger(route_stderr=output_format == "json"),
        lintro_config=config,
        clean_stdout_output=output_format == "json",
    )

    artifact = tool_executor.execute_run(
        ctx=ctx,
        paths=[str(unreadable_source)],
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format=output_format,
        verbose=False,
        yes=True,
    )
    render_run(artifact, ctx=ctx, output_format=output_format)
    stdout = capsys.readouterr().out
    report = json.loads(
        (tmp_path / ".lintro/artifacts/json/results.json").read_text(encoding="utf-8"),
    )

    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.total_issues).is_equal_to(remaining + 2)
    assert_that(artifact.total_fixed).is_equal_to(fixed)
    assert_that(artifact.tool_results[0]).is_same_as(primary)
    assert_that(report["results"][0]["output"]).is_equal_to(primary.output)
    if remaining:
        assert_that(report["results"][0]["issues"][0]["message"]).is_equal_to(
            "unused import",
        )
    failures = [r for r in report["results"] if r["tool"] == "module-size"]
    assert_that(failures).is_not_empty()
    assert_that(all(not result["success"] for result in failures)).is_true()
    failure_text = "\n".join(result["output"] for result in failures)
    assert_that(failure_text).contains(
        "denied.py",
        "permission denied",
        "disappeared.py",
        "file disappeared",
    )
    assert_that(report["summary"]["total_issues"]).is_equal_to(remaining + 2)
    if output_format == "json":
        document = json.loads(stdout)
        assert_that(document["results"]).is_equal_to(report["results"])
    else:
        assert_that(stdout).contains(
            "TOTALS",
            "module-size",
            "ruff",
            "permission denied",
        )
    if action == Action.FIX:
        assert_that(artifact.total_remaining).is_equal_to(remaining)
        assert_that(module.read_text(encoding="utf-8")).is_equal_to("x = 1\n")
        assert_that(report["summary"]["total_fixed"]).is_equal_to(2)
        assert_that(report["results"][0]["fixed"]).is_equal_to(2)
        assert_that(report["results"][0]["remaining"]).is_equal_to(remaining)


@pytest.mark.parametrize(
    "control",
    [
        "empty-paths",
        "empty-dir",
        "non-python",
        "empty-module",
        "excluded",
        "config-excluded",
        "baselined",
        "disabled",
        "test-action",
    ],
)
@pytest.mark.parametrize("output_format", ["plain", "json"])
def test_module_size_gate_controls_remain_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    control: str,
    output_format: str,
) -> None:
    """No-target scans and intentionally skipped unreadable files stay quiet.

    Args:
        tmp_path: Workspace for the control case.
        monkeypatch: Pytest patch fixture.
        capsys: Captured gate output.
        control: Empty, irrelevant, excluded or baselined scan target.
        output_format: Human or JSON mode.
    """
    module = tmp_path / "ignored.py"
    config: dict[str, object] = {}
    exclude = None
    if control == "non-python":
        (tmp_path / "notes.txt").write_text("not a module\n", encoding="utf-8")
    elif control not in ("empty-paths", "empty-dir"):
        module.touch()
    if control == "excluded":
        exclude = "ignored.py"
    elif control == "config-excluded":
        config["exclude"] = ["ignored.py"]
    elif control == "baselined":
        config["baseline"] = ["ignored.py"]
    elif control == "disabled":
        config["enabled"] = False
    monkeypatch.setattr(gates, "load_module_size_config", lambda: config)
    monkeypatch.setattr(gates, "load_lintro_tool_config", lambda _name: {})
    results: list[ToolResult] = []
    read_error = PermissionError(13, "permission denied", str(module))
    unreadable = control in (
        "excluded",
        "config-excluded",
        "baselined",
        "disabled",
        "test-action",
    )
    with patch(
        "lintro.utils.module_size.open",
        side_effect=read_error if unreadable else None,
        wraps=open,
    ):
        total = gates.execute_gates(
            action=Action.TEST if control == "test-action" else Action.CHECK,
            paths=[] if control == "empty-paths" else [str(tmp_path)],
            exclude=exclude,
            include_venv=False,
            output_format=output_format,
            logger=ThreadSafeConsoleLogger(),
            all_results=results,
            total_issues=0,
        )

    assert_that(total).is_zero()
    assert_that(results).is_empty()
    assert_that(capsys.readouterr().out).is_empty()


def test_measured_oversized_module_is_warning_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A measurable size violation warns without making the run fail.

    Args:
        tmp_path: Workspace for an oversized module.
        monkeypatch: Pytest patch fixture.
        capsys: Captured gate warnings.
    """
    (tmp_path / "large.py").write_bytes(b"# coding: latin-1\n# tea\xe9\n")
    monkeypatch.setattr(gates, "load_module_size_config", lambda: {"threshold": 1})
    monkeypatch.setattr(gates, "load_lintro_tool_config", lambda _name: {})
    results: list[ToolResult] = []

    total = gates.execute_gates(
        action=Action.CHECK,
        paths=[str(tmp_path)],
        exclude=None,
        include_venv=False,
        output_format="plain",
        logger=ThreadSafeConsoleLogger(),
        all_results=results,
        total_issues=0,
    )

    assert_that(total).is_zero()
    assert_that(results).is_empty()
    assert_that(capsys.readouterr().out).contains("large.py (2 lines)", "non-blocking")
