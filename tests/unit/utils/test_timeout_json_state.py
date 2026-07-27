"""Tests for tool-timeout state in the JSON report (#1768).

A CI consumer must be able to tell a tool *timeout* from a genuine lint
finding using evidence about its own run. These tests pin the three pieces
that make that possible:

- ``timed_out`` is serialized per tool,
- timeout accounting is identical for every tool (a timeout is an execution
  failure, never a finding, so it never reaches ``summary.total_issues``),
- the ``json`` artifact is auto-emitted under GitHub Actions alongside SARIF.

Coverage spans all four run shapes the classifier must separate: timeout with
no other findings, timeout alongside a real finding, a non-timeout tool
failure, and a clean run — exercised through the real ``mypy`` and
``prettier`` plugins, which historically disagreed about timeout accounting.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only TimeoutExpired is referenced, nothing spawns
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.definitions.mypy import MypyPlugin
from lintro.tools.definitions.prettier import PrettierPlugin
from lintro.utils.execution.exit_codes import (
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.json_output import (
    create_json_output,
    serialize_tool_result,
    timed_out_tool_names,
)
from lintro.utils.tool_executor import _run_fix_with_retry, _write_artifacts


@dataclass
class _StubIssue(BaseIssue):
    """Minimal issue standing in for a genuine lint finding."""

    file: str = "src/main.py"
    line: int = 7
    code: str = "E001"
    message: str = "genuine finding"


def _raise_timeout(*args: object, **kwargs: object) -> tuple[bool, str]:
    """Raise the timeout the tools catch, standing in for a slow subprocess.

    Args:
        *args: Ignored positional arguments.
        **kwargs: Ignored keyword arguments.

    Returns:
        Never returns; the signature matches ``_run_subprocess`` so it can
        stand in for it.

    Raises:
        subprocess.TimeoutExpired: Always.
    """
    raise subprocess.TimeoutExpired(cmd=["stub"], timeout=1)


def _assume_tool_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the pre-run version gate so the timeout path is reached.

    ``prepare_execution`` verifies the tool binary before running it and
    returns a ``skipped=True`` early result when it is missing. Test runners
    without the npm-managed binaries (prettier, oxlint, ...) would therefore
    never reach the subprocess stub, and the run would be reported as skipped
    and successful rather than timed out. Stubbing the gate keeps these tests
    hermetic and asserting the timeout contract on every runner.

    Args:
        monkeypatch: Fixture used to stub the version gate.
    """
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda definition, cwd=None: None,
    )


def _timed_out_mypy_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ToolResult:
    """Run the real mypy plugin against a subprocess that always times out.

    Args:
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the Python file handed to the plugin.

    Returns:
        The plugin's ``ToolResult`` for the timed-out run.
    """
    target = tmp_path / "sample.py"
    target.write_text("x: int = 1\n", encoding="utf-8")
    _assume_tool_installed(monkeypatch)
    plugin = MypyPlugin()
    monkeypatch.setattr(plugin, "_run_subprocess", _raise_timeout)
    return plugin.check(paths=[str(target)], options={})


def _timed_out_prettier_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ToolResult:
    """Run the real prettier plugin against a subprocess that always times out.

    Args:
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the JSON file handed to the plugin.

    Returns:
        The plugin's ``ToolResult`` for the timed-out run.
    """
    target = tmp_path / "sample.json"
    target.write_text('{"a":1}\n', encoding="utf-8")
    _assume_tool_installed(monkeypatch)
    plugin = PrettierPlugin()
    monkeypatch.setattr(plugin, "_run_subprocess", _raise_timeout)
    return plugin.check(paths=[str(target)], options={})


def _make_config(*, artifacts: list[str] | None = None) -> MagicMock:
    """Build a minimal ``LintroConfig``-like stub.

    Args:
        artifacts: Formats to place in ``execution.artifacts``.

    Returns:
        A mock exposing only the attribute ``_write_artifacts`` reads.
    """
    cfg = MagicMock()
    cfg.execution.artifacts = artifacts or []
    return cfg


def _json_for(results: list[ToolResult]) -> dict[str, Any]:
    """Render the stdout JSON payload for a set of results.

    Args:
        results: Tool results making up the run.

    Returns:
        The JSON-serializable payload, with ``summary`` totals aggregated the
        same way the real executor aggregates them.
    """
    total_issues, total_fixed, total_remaining = aggregate_tool_results(
        results,
        Action.CHECK,
    )
    return create_json_output(
        action=Action.CHECK,
        results=results,
        total_issues=total_issues,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
        exit_code=determine_exit_code(
            action=Action.CHECK,
            all_results=results,
            total_issues=total_issues,
            total_remaining=total_remaining,
            main_phase_empty_due_to_filter=False,
        ),
    )


# ---------------------------------------------------------------------------
# Per-tool serialization
# ---------------------------------------------------------------------------


def test_serialize_tool_result_reports_timed_out_false_by_default() -> None:
    """A result that did not time out serializes ``timed_out: false``."""
    result = ToolResult(name="ruff", success=True, issues_count=0)

    data = serialize_tool_result(result, action=Action.CHECK)

    assert_that(data).contains_key("timed_out")
    assert_that(data["timed_out"]).is_false()


def test_serialize_tool_result_reports_timed_out_true() -> None:
    """A timed-out result serializes ``timed_out: true``."""
    result = ToolResult(
        name="mypy",
        success=False,
        issues_count=0,
        timed_out=True,
    )

    data = serialize_tool_result(result, action=Action.CHECK)

    assert_that(data["timed_out"]).is_true()


# ---------------------------------------------------------------------------
# Consistent accounting across tools that historically disagreed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "factory"),
    [
        ("mypy", _timed_out_mypy_result),
        ("prettier", _timed_out_prettier_result),
    ],
)
def test_timeout_is_not_counted_as_an_issue(
    tool_name: str,
    factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every tool accounts for a timeout identically: failure, zero issues.

    Args:
        tool_name: Name the plugin reports itself under.
        factory: Helper producing a timed-out result for that plugin.
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the file handed to the plugin.
    """
    result = factory(monkeypatch, tmp_path)

    assert_that(result.name).is_equal_to(tool_name)
    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(list(result.issues or [])).is_empty()

    data = serialize_tool_result(result, action=Action.CHECK)
    assert_that(data["timed_out"]).is_true()
    assert_that(data["issues_count"]).is_equal_to(0)
    assert_that(data).does_not_contain_key("issues")


# ---------------------------------------------------------------------------
# The four run shapes a downstream classifier must separate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [_timed_out_mypy_result, _timed_out_prettier_result],
)
def test_timeout_with_no_other_findings_reports_zero_issues(
    factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A run whose only failure is a timeout reports zero genuine issues.

    Args:
        factory: Helper producing a timed-out result for a plugin.
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the file handed to the plugin.
    """
    timed_out = factory(monkeypatch, tmp_path)
    clean = ToolResult(name="ruff", success=True, issues_count=0)
    results = [timed_out, clean]

    payload = _json_for(results)

    assert_that(payload["summary"]["total_issues"]).is_equal_to(0)
    assert_that(payload["summary"]["total_remaining"]).is_equal_to(0)
    assert_that(payload["summary"]["timed_out_tools"]).is_equal_to([timed_out.name])


@pytest.mark.parametrize(
    "factory",
    [_timed_out_mypy_result, _timed_out_prettier_result],
)
def test_timeout_alongside_a_real_finding_keeps_the_finding(
    factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A genuine finding survives a concurrent timeout and stays counted.

    Args:
        factory: Helper producing a timed-out result for a plugin.
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the file handed to the plugin.
    """
    timed_out = factory(monkeypatch, tmp_path)
    finding = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[_StubIssue()],
    )
    results = [timed_out, finding]

    payload = _json_for(results)

    assert_that(payload["summary"]["total_issues"]).is_equal_to(1)
    assert_that(payload["summary"]["timed_out_tools"]).is_equal_to([timed_out.name])
    by_tool = {entry["tool"]: entry for entry in payload["results"]}
    assert_that(by_tool[timed_out.name]["issues_count"]).is_equal_to(0)
    assert_that(by_tool["ruff"]["issues_count"]).is_equal_to(1)
    assert_that(by_tool["ruff"]["timed_out"]).is_false()


def test_non_timeout_tool_failure_is_not_reported_as_a_timeout() -> None:
    """A tool that failed without timing out reports no timeout at all."""
    crashed = ToolResult(
        name="mypy",
        success=False,
        issues_count=0,
        output="mypy execution failed: config error",
    )
    results = [crashed, ToolResult(name="ruff", success=True, issues_count=0)]

    payload = _json_for(results)

    assert_that(payload["summary"]["total_issues"]).is_equal_to(0)
    assert_that(payload["summary"]["timed_out_tools"]).is_empty()
    by_tool = {entry["tool"]: entry for entry in payload["results"]}
    assert_that(by_tool["mypy"]["timed_out"]).is_false()
    assert_that(by_tool["mypy"]["success"]).is_false()


def test_clean_run_reports_no_timeouts_and_no_issues() -> None:
    """A clean run reports zero issues and an empty timeout list."""
    results = [
        ToolResult(name="mypy", success=True, issues_count=0),
        ToolResult(name="prettier", success=True, issues_count=0),
    ]

    payload = _json_for(results)

    assert_that(payload["summary"]["total_issues"]).is_equal_to(0)
    assert_that(payload["summary"]["timed_out_tools"]).is_empty()
    assert_that([entry["timed_out"] for entry in payload["results"]]).does_not_contain(
        True,
    )


# ---------------------------------------------------------------------------
# The timeout must still fail the run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [_timed_out_mypy_result, _timed_out_prettier_result],
)
def test_timeout_still_fails_the_run_despite_zero_issues(
    factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dropping the pseudo-issue must not turn a timed-out run green.

    Args:
        factory: Helper producing a timed-out result for a plugin.
        monkeypatch: Fixture used to stub the plugin's subprocess runner.
        tmp_path: Directory holding the file handed to the plugin.
    """
    results = [factory(monkeypatch, tmp_path)]
    total_issues, _, total_remaining = aggregate_tool_results(results, Action.CHECK)

    exit_code = determine_exit_code(
        action=Action.CHECK,
        all_results=results,
        total_issues=total_issues,
        total_remaining=total_remaining,
        main_phase_empty_due_to_filter=False,
    )

    assert_that(total_issues).is_equal_to(0)
    assert_that(exit_code).is_equal_to(1)


# ---------------------------------------------------------------------------
# summary.timed_out_tools helper
# ---------------------------------------------------------------------------


def test_timed_out_tool_names_preserves_order_and_deduplicates() -> None:
    """Timed-out tool names are listed once each, in execution order."""
    results = [
        ToolResult(name="mypy", success=False, issues_count=0, timed_out=True),
        ToolResult(name="ruff", success=True, issues_count=0),
        ToolResult(name="mypy", success=False, issues_count=0, timed_out=True),
        ToolResult(name="prettier", success=False, issues_count=0, timed_out=True),
    ]

    assert_that(timed_out_tool_names(results)).is_equal_to(["mypy", "prettier"])


# ---------------------------------------------------------------------------
# GitHub Actions auto-emission of the json artifact
# ---------------------------------------------------------------------------


def test_json_artifact_auto_emits_in_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under GHA the json artifact lands beside SARIF without extra config.

    Args:
        tmp_path: Directory the artifacts are written beneath.
        monkeypatch: Fixture used to set ``GITHUB_ACTIONS`` and the cwd.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.chdir(tmp_path)

    results = [
        ToolResult(name="mypy", success=False, issues_count=0, timed_out=True),
        ToolResult(name="ruff", success=True, issues_count=0),
    ]
    _write_artifacts(
        results,
        _make_config(),
        MagicMock(),
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    json_path = tmp_path / ".lintro" / "artifacts" / "json" / "results.json"
    sarif_path = tmp_path / ".lintro" / "artifacts" / "sarif" / "results.sarif.json"
    assert_that(json_path.exists()).is_true()
    assert_that(sarif_path.exists()).is_true()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert_that(data["summary"]["total_issues"]).is_equal_to(0)
    assert_that(data["summary"]["timed_out_tools"]).is_equal_to(["mypy"])
    by_tool = {entry["tool"]: entry for entry in data["results"]}
    assert_that(by_tool["mypy"]["timed_out"]).is_true()
    assert_that(by_tool["ruff"]["timed_out"]).is_false()


def test_json_artifact_not_emitted_outside_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside GHA nothing is emitted unless the config asks for it.

    Args:
        tmp_path: Directory the artifacts would be written beneath.
        monkeypatch: Fixture used to clear ``GITHUB_ACTIONS`` and set the cwd.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    _write_artifacts(
        [ToolResult(name="ruff", success=True, issues_count=0)],
        _make_config(),
        MagicMock(),
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that((tmp_path / ".lintro" / "artifacts").exists()).is_false()


def test_json_artifact_not_duplicated_when_already_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``json`` artifact is not requested twice under GHA.

    Args:
        tmp_path: Directory the artifacts are written beneath.
        monkeypatch: Fixture used to set ``GITHUB_ACTIONS`` and the cwd.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.chdir(tmp_path)

    logger = MagicMock()
    _write_artifacts(
        [ToolResult(name="ruff", success=True, issues_count=0)],
        _make_config(artifacts=["json"]),
        logger,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that(
        (tmp_path / ".lintro" / "artifacts" / "json" / "results.json").exists(),
    ).is_true()
    logger.console_output.assert_not_called()


# ---------------------------------------------------------------------------
# The flag must survive result post-processing
# ---------------------------------------------------------------------------


def _timing_out_fix_tool(initial_issues_count: int) -> Any:
    """Build a fix-capable tool whose every pass times out.

    Args:
        initial_issues_count: Genuine issues detected before the timeout.

    Returns:
        A stub exposing the ``fix`` shape the real implementations return on
        timeout, with pre-timeout counts populated.
    """
    tool = MagicMock()
    tool.definition.name = "ruff"
    tool.fix.return_value = ToolResult(
        name="ruff",
        success=False,
        output="ruff execution timed out (1s limit exceeded)",
        issues_count=initial_issues_count,
        issues=[],
        initial_issues_count=initial_issues_count,
        fixed_issues_count=0,
        remaining_issues_count=initial_issues_count,
        timed_out=True,
    )
    return tool


@pytest.mark.parametrize("initial_issues_count", [0, 3])
def test_fix_retry_merge_preserves_timed_out(initial_issues_count: int) -> None:
    """The retry merge must not erase ``timed_out``.

    ``_run_fix_with_retry`` rebuilds the ``ToolResult`` field-by-field after
    the final pass. Omitting ``timed_out`` there would make every ``lintro
    fmt`` run report ``timed_out: false`` for a tool that really did time out
    — a false negative worse than the original bug, because a consumer would
    read an infrastructure flake as a genuine lint failure.

    Args:
        initial_issues_count: Pre-timeout issue count, exercising both the
            zero and non-zero merge branches.
    """
    tool = _timing_out_fix_tool(initial_issues_count=initial_issues_count)

    merged = _run_fix_with_retry(tool=tool, paths=[], options={}, max_retries=3)

    assert_that(merged.timed_out).is_true()
    assert_that(merged.success).is_false()
    assert_that(serialize_tool_result(merged, action=Action.FIX)["timed_out"]).is_true()
    assert_that(timed_out_tool_names([merged])).is_equal_to(["ruff"])


def test_fix_retry_merge_leaves_clean_result_not_timed_out() -> None:
    """A successful fix run must not be mislabelled as timed out."""
    tool = MagicMock()
    tool.definition.name = "ruff"
    tool.fix.return_value = ToolResult(
        name="ruff",
        success=True,
        output="",
        issues_count=0,
        issues=[],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
    )

    merged = _run_fix_with_retry(tool=tool, paths=[], options={}, max_retries=3)

    assert_that(merged.timed_out).is_false()
    assert_that(timed_out_tool_names([merged])).is_empty()
