"""Additional tests for `lintro.utils.tool_executor` coverage.

These tests focus on unhit branches in the simple executor:
- `_get_tools_to_run` edge cases and validation
- Main-loop error handling when resolving tools
- Early post-checks filtering removing tools from the main phase
- Post-checks behavior for unknown tool names
- Output persistence error handling
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Never

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    pass

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


def _stub_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    import lintro.utils.console as cl

    class SilentLogger:
        def __getattr__(
            self,
            name: str,
        ) -> Callable[..., None]:
            def _(*a: Any, **k: Any) -> None:
                return None

            return _

    monkeypatch.setattr(cl, "create_logger", lambda *_a, **_k: SilentLogger())


def test_get_tools_to_run_unknown_tool_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown tool name should raise ValueError.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.

    Raises:
        AssertionError: If the expected ValueError is not raised.
    """
    from lintro.utils.execution import tool_configuration as tc

    _stub_logger(monkeypatch)

    # Use real function; only patch manager lookups to be harmless if called
    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: {},
        raising=True,
    )

    try:
        _ = tc.get_tools_to_run(tools="notatool", action="check")
        raise AssertionError("Expected ValueError for unknown tool")
    except ValueError as e:
        assert_that(str(e)).contains("Unknown tool")


def test_get_tools_to_run_fmt_with_cannot_fix_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a non-fix tool for fmt should raise a validation error.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.

    Raises:
        AssertionError: If the expected ValueError is not raised.
    """
    from lintro.utils.execution import tool_configuration as tc

    _stub_logger(monkeypatch)

    class NoFixTool:
        def __init__(self) -> None:
            self._definition = FakeToolDefinition(name="bandit", can_fix=False)

        @property
        def definition(self) -> FakeToolDefinition:
            return self._definition

        @property
        def can_fix(self) -> bool:
            return self._definition.can_fix

        def set_options(self, **kwargs: Any) -> None:
            return None

    # Ensure we resolve a tool instance with can_fix False
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: NoFixTool(),
        raising=True,
    )

    # Directly call the helper
    try:
        _ = tc.get_tools_to_run(tools="bandit", action="fmt")
        raise AssertionError("Expected ValueError for non-fix tool in fmt")
    except ValueError as e:
        assert_that(str(e)).contains("does not support formatting")


def test_main_loop_get_tool_raises_appends_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If a tool cannot be resolved, a failure result is appended and run continues.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
        capsys: Pytest fixture to capture stdout/stderr for assertions.
    """
    _stub_logger(monkeypatch)

    ok = ToolResult(name="black", success=True, output="", issues_count=0)

    def fake_get_tools(
        _tools: str | None,
        _action: str,
        **_kwargs: object,
    ) -> ToolsToRunResult:
        return ToolsToRunResult(to_run=["ruff", "black"])

    def fake_get_tool(name: str) -> object:
        if name == "ruff":
            raise RuntimeError("ruff not available")
        return type(
            "_T",
            (),
            {  # simple stub
                "name": "black",
                "definition": FakeToolDefinition(name="black", can_fix=True),
                "can_fix": True,
                "set_options": lambda _self, **k: None,
                "reset_options": lambda _self: None,
                "copy_for_execution": lambda _self: _self,
                "check": lambda _self, paths, options=None: ok,
                "fix": lambda _self, paths, options=None: ok,
                "options": {},
            },
        )()

    monkeypatch.setattr(te, "get_tools_to_run", fake_get_tools, raising=True)
    monkeypatch.setattr(tool_manager, "get_tool", fake_get_tool, raising=True)
    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        lambda self, results: None,
        raising=True,
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
    out = capsys.readouterr().out
    data = json.loads(out)
    results = {r.get("tool"): r for r in data.get("results", [])}
    # Two selected tools take the real parallel dispatcher, which must turn an
    # unresolvable tool into a failed result rather than aborting the run, and
    # must still run the tool that resolved.
    assert_that(results).contains_key("ruff", "black")
    assert_that(results["ruff"].get("success")).is_false()
    assert_that(results["black"].get("success")).is_true()
    # Exit should be failure due to appended failure result
    assert_that(code).is_equal_to(1)


def test_write_reports_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors while saving outputs should not crash or change exit semantics.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
    """
    _stub_logger(monkeypatch)

    ok = ToolResult(name="ruff", success=True, output="", issues_count=0)

    def fake_get_tools(
        _tools: str | None,
        _action: str,
        **_kwargs: object,
    ) -> ToolsToRunResult:
        return ToolsToRunResult(to_run=["ruff"])

    ruff_tool = type(
        "_T",
        (),
        {
            "name": "ruff",
            "definition": FakeToolDefinition(name="ruff", can_fix=True),
            "can_fix": True,
            "set_options": lambda _self, **k: None,
            "reset_options": lambda _self: None,
            "copy_for_execution": lambda _self: _self,
            "check": lambda _self, paths, options=None: ok,
            "fix": lambda _self, paths, options=None: ok,
            "options": {},
        },
    )()

    monkeypatch.setattr(te, "get_tools_to_run", fake_get_tools, raising=True)
    monkeypatch.setattr(tool_manager, "get_tool", lambda name: ruff_tool)

    def boom(self: object, results: list[ToolResult]) -> Never:
        raise OSError("disk full")

    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        boom,
        raising=True,
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
