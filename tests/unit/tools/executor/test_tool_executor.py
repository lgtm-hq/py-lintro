"""Unit tests for main tool executor: success/failure and JSON outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.output import OutputManager
from lintro.utils.tool_executor import run_lint_tools_simple


@dataclass
class FakeToolDefinition:
    """Fake ToolDefinition for testing."""

    name: str
    can_fix: bool = False
    description: str = ""
    file_patterns: list[str] = field(default_factory=list)
    native_configs: list[str] = field(default_factory=list)


class FakeTool:
    """Simple tool stub returning a pre-baked ToolResult."""

    def __init__(self, name: str, can_fix: bool, result: ToolResult) -> None:
        """Initialize the fake tool.

        Args:
            name: Tool name.
            can_fix: Whether fixes are supported.
            result: Result object to return from check/fix.
        """
        self.name = name
        self._definition = FakeToolDefinition(name=name, can_fix=can_fix)
        self._result = result
        self.options: dict[str, Any] = {}

    @property
    def definition(self) -> FakeToolDefinition:
        """Return the tool definition.

        Returns:
            FakeToolDefinition containing tool metadata.
        """
        return self._definition

    @property
    def can_fix(self) -> bool:
        """Return whether the tool can fix issues.

        Returns:
            True if the tool can fix issues.
        """
        return self._definition.can_fix

    def reset_options(self) -> None:
        """Reset options to defaults (stub for testing)."""
        self.options = {}

    def set_options(self, **kwargs: Any) -> None:
        """Record option values provided to the tool stub.

        Args:
            **kwargs: Arbitrary options to store for assertions.
        """
        self.options.update(kwargs)

    def check(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Return the stored result for a check invocation.

        Args:
            paths: Target paths (ignored in stub).
            options: Optional tool options.

        Returns:
            ToolResult: Pre-baked result instance.
        """
        return self._result

    def fix(
        self,
        paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Return the stored result for a fix invocation.

        Args:
            paths: Target paths (ignored in stub).
            options: Optional tool options.

        Returns:
            ToolResult: Pre-baked result instance.
        """
        return self._result


def _setup_tool_manager(
    monkeypatch: pytest.MonkeyPatch,
    tools: dict[str, FakeTool],
) -> None:
    """Configure tool manager stubs to return provided tools.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tools: Mapping of tool name to FakeTool instance.
    """
    tools_dict = tools

    def fake_get_tools(
        tools: str | None,
        action: str,
        **_kwargs: object,
    ) -> ToolsToRunResult:
        return ToolsToRunResult(to_run=list(tools_dict.keys()))

    monkeypatch.setattr(te, "get_tools_to_run", fake_get_tools, raising=True)

    def fake_get_tool(name: str) -> FakeTool:
        return tools_dict[name.lower()]

    monkeypatch.setattr(tool_manager, "get_tool", fake_get_tool, raising=True)

    def noop_write_reports_from_results(
        self: Any,
        results: list[ToolResult],
    ) -> None:
        return None

    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        noop_write_reports_from_results,
        raising=True,
    )


def _stub_logger(monkeypatch: pytest.MonkeyPatch, fake_logger: Any) -> None:
    """Patch create_logger to return a FakeLogger instance.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: FakeLogger fixture instance.
    """
    import lintro.utils.console as cl

    monkeypatch.setattr(cl, "create_logger", lambda **k: fake_logger, raising=True)


def test_executor_check_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
) -> None:
    """Exit with 0 when check succeeds and has zero issues.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: FakeLogger fixture.
    """
    _stub_logger(monkeypatch, fake_logger)
    result = ToolResult(name="ruff", success=True, output="", issues_count=0)
    _setup_tool_manager(
        monkeypatch,
        {"ruff": FakeTool("ruff", can_fix=True, result=result)},
    )
    code = run_lint_tools_simple(
        action="check",
        paths=["."],
        tools="all",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
    )
    assert_that(code).is_equal_to(0)


def test_executor_check_failure(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
) -> None:
    """Exit with 1 when check succeeds but issues are reported.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: FakeLogger fixture.
    """
    _stub_logger(monkeypatch, fake_logger)
    result = ToolResult(name="ruff", success=True, output="something", issues_count=2)
    _setup_tool_manager(
        monkeypatch,
        {"ruff": FakeTool("ruff", can_fix=True, result=result)},
    )
    code = run_lint_tools_simple(
        action="check",
        paths=["."],
        tools="all",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=True,
        raw_output=False,
    )
    assert_that(code).is_equal_to(1)


def test_executor_fmt_success_with_counts(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
) -> None:
    """Exit with 0 when format succeeds and counts are populated.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: Fake logger fixture.
    """
    _stub_logger(monkeypatch, fake_logger)
    result = ToolResult(
        name="prettier",
        success=True,
        output="Fixed 2 issue(s)\nFound 0 issue(s) that cannot be auto-fixed",
        issues_count=0,
        fixed_issues_count=2,
        remaining_issues_count=0,
    )
    _setup_tool_manager(
        monkeypatch,
        {"prettier": FakeTool("prettier", can_fix=True, result=result)},
    )
    code = run_lint_tools_simple(
        action="fmt",
        paths=["."],
        tools="all",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
    )
    assert_that(code).is_equal_to(0)


def test_executor_json_output(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit JSON output containing action and results when requested.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: Fake logger fixture.
        capsys: Pytest capture fixture for stdout.
    """
    _stub_logger(monkeypatch, fake_logger)
    t1 = ToolResult(name="ruff", success=True, output="", issues_count=1)
    t2 = ToolResult(
        name="prettier",
        success=True,
        output="",
        issues_count=0,
        fixed_issues_count=1,
        remaining_issues_count=0,
    )
    _setup_tool_manager(
        monkeypatch,
        {
            "ruff": FakeTool("ruff", can_fix=True, result=t1),
            "prettier": FakeTool("prettier", can_fix=True, result=t2),
        },
    )
    code = run_lint_tools_simple(
        action="check",
        paths=["."],
        tools="all",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        raw_output=False,
    )
    assert_that(code).is_equal_to(1)
    out = capsys.readouterr().out
    data = json.loads(out)
    # JSON output includes results and summary sections
    assert_that("results" in data and len(data["results"]) >= 2).is_true()
    assert_that("summary" in data).is_true()


def test_executor_handles_tool_failure_with_output(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
    tmp_path: Path,
) -> None:
    """Return non-zero when a tool fails but emits output (coverage branch).

    Args:
        monkeypatch: pytest monkeypatch fixture
        fake_logger: Fake logger fixture.
        tmp_path: pytest tmp_path fixture
    """
    _stub_logger(monkeypatch, fake_logger)
    failing = ToolResult(name="bandit", success=False, output="oops", issues_count=0)
    _setup_tool_manager(
        monkeypatch,
        {"bandit": FakeTool("bandit", can_fix=False, result=failing)},
    )
    code = run_lint_tools_simple(
        action="check",
        paths=[str(tmp_path)],
        tools="all",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="plain",
        verbose=False,
        raw_output=False,
    )
    assert_that(code).is_equal_to(1)


def test_parse_tool_options_typed_values() -> None:
    """Ensure --tool-options parsing coerces values into proper types.

    Supported coercions:
    - booleans (True/False)
    - None/null
    - integers/floats
    - lists via pipe separation (E|F|W)
    """
    opt_str = (
        "ruff:unsafe_fixes=True,ruff:line_length=88,ruff:target_version=py313,"
        "ruff:select=E|F,prettier:verbose_fix_output=false,yamllint:strict=None,"
        "ruff:ratio=0.5"
    )
    from lintro.utils.tool_options import parse_tool_options

    parsed = parse_tool_options(opt_str)
    assert_that(
        isinstance(parsed["ruff"]["unsafe_fixes"], bool)
        and parsed["ruff"]["unsafe_fixes"],
    ).is_true()
    assert_that(
        isinstance(parsed["ruff"]["line_length"], int)
        and parsed["ruff"]["line_length"] == 88,
    ).is_true()
    assert_that(parsed["ruff"]["target_version"]).is_equal_to("py313")
    assert_that(parsed["ruff"]["select"]).is_equal_to(["E", "F"])
    assert_that(parsed["prettier"]["verbose_fix_output"] is False).is_true()
    assert_that(parsed["yamllint"]["strict"]).is_none()
    assert_that(
        isinstance(parsed["ruff"]["ratio"], float) and parsed["ruff"]["ratio"] == 0.5,
    ).is_true()


def test_executor_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
    fake_logger: Any,
) -> None:
    """Exit with 1 when an unknown tool is requested.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_logger: Fake logger fixture.
    """
    _stub_logger(monkeypatch, fake_logger)

    def raise_value_error(tools: str | None, action: str, **_kwargs: object) -> Never:
        raise ValueError("unknown tool")

    monkeypatch.setattr(te, "get_tools_to_run", raise_value_error, raising=True)
    code = run_lint_tools_simple(
        action="check",
        paths=["."],
        tools="unknown",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
    )
    assert_that(code).is_equal_to(1)
