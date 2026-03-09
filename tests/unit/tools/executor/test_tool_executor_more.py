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
        ) -> Callable[..., None]:  # noqa: D401 - test stub
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
    except ValueError as e:  # noqa: PT017
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

        def set_options(self, **kwargs: Any) -> None:  # noqa: D401
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
    except ValueError as e:  # noqa: PT017
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
        tools: str | None,
        action: str,
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
                "set_options": lambda self, **k: None,
                "check": lambda self, paths, options=None: ok,
                "fix": lambda self, paths, options=None: ok,
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
    tool_names = [r.get("tool") for r in data.get("results", [])]
    assert_that("ruff" in tool_names).is_true()
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
        tools: str | None,
        action: str,
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
            "set_options": lambda self, **k: None,
            "check": lambda self, paths, options=None: ok,
            "fix": lambda self, paths, options=None: ok,
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


def test_unknown_post_check_tool_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown post-check tool names should be warned and skipped gracefully.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
    """
    _stub_logger(monkeypatch)

    import lintro.utils.tool_executor as te

    ok = ToolResult(name="ruff", success=True, output="", issues_count=0)
    ruff_tool = type(
        "_T",
        (),
        {
            "name": "ruff",
            "definition": FakeToolDefinition(name="ruff", can_fix=True),
            "can_fix": True,
            "set_options": lambda self, **k: None,
            "check": lambda self, paths, options=None: ok,
            "fix": lambda self, paths, options=None: ok,
            "options": {},
        },
    )()

    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff"]),
        raising=True,
    )
    monkeypatch.setattr(tool_manager, "get_tool", lambda name: ruff_tool)
    monkeypatch.setattr(
        te,
        "load_post_checks_config",
        lambda: {"enabled": True, "tools": ["notatool"], "enforce_failure": False},
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


def test_post_checks_early_filter_removes_black_from_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Black should be excluded from main phase when configured as post-check.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
    """
    import lintro.utils.config as cfg
    import lintro.utils.post_checks as pc
    import lintro.utils.tool_executor as te

    class LoggerCapture:
        def __init__(self) -> None:
            self.tools_list: list[str] | None = None
            self.run_dir: str | None = None

        def __getattr__(self, name: str) -> Callable[..., None]:  # default no-ops
            def _(*a: Any, **k: Any) -> None:
                return None

            return _

        def print_lintro_header(self) -> None:
            return None

        def print_tool_header(self, tool_name: str, action: str) -> None:
            # Capture tool names that get executed
            if self.tools_list is None:
                self.tools_list = []
            self.tools_list.append(tool_name)
            return None

    logger = LoggerCapture()
    from lintro.utils import console

    monkeypatch.setattr(
        console,
        "create_logger",
        lambda **k: logger,
        raising=True,
    )

    # Tools initially include ruff and black
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff", "black"]),
        raising=True,
    )

    def post_check_config():
        return {"enabled": True, "tools": ["black"], "enforce_failure": True}

    # Early config marks black as post-check
    # Must patch in all modules that import load_post_checks_config
    monkeypatch.setattr(cfg, "load_post_checks_config", post_check_config, raising=True)
    monkeypatch.setattr(te, "load_post_checks_config", post_check_config, raising=True)
    monkeypatch.setattr(pc, "load_post_checks_config", post_check_config, raising=True)

    # Provide a no-op ruff tool
    ok = ToolResult(name="ruff", success=True, output="", issues_count=0)
    ruff_tool = type(
        "_T",
        (),
        {
            "name": "ruff",
            "definition": FakeToolDefinition(name="ruff", can_fix=True),
            "can_fix": True,
            "set_options": lambda self, **k: None,
            "check": lambda self, paths, options=None: ok,
            "fix": lambda self, paths, options=None: ok,
            "options": {},
        },
    )()
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: ruff_tool,
        raising=True,
    )
    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        lambda self, results: None,
        raising=True,
    )

    # Mock execute_post_checks to not run any post-checks
    # (we're only testing that black is filtered from the main phase)
    def mock_execute_post_checks(**kwargs: Any) -> tuple[int, int, int]:
        return (
            kwargs.get("total_issues", 0),
            kwargs.get("total_fixed", 0),
            kwargs.get("total_remaining", 0),
        )

    monkeypatch.setattr(
        te,
        "execute_post_checks",
        mock_execute_post_checks,
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
    # Ensure black is not in main-phase tool headers (only ruff should run)
    assert_that(logger.tools_list).is_not_none()
    assert_that(logger.tools_list).is_equal_to(["ruff"])


def test_all_filtered_results_in_no_tools_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If filtering removes all tools, executor should return failure gracefully.

    When all selected tools are configured as post-checks (filtered from main phase)
    but post-checks don't actually produce results, the executor should return 1.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
    """
    _stub_logger(monkeypatch)

    import lintro.utils.tool_executor as te

    # Mock config that filters out all tools to post-checks
    mock_config = {"enabled": True, "tools": ["black"], "enforce_failure": True}

    # Start with only black
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["black"]),
        raising=True,
    )
    # Early config filters out black
    monkeypatch.setattr(te, "load_post_checks_config", lambda: mock_config)
    # Mock execute_post_checks to do nothing (simulates post-checks not running)
    # Returns (total_issues, total_fixed, total_remaining)
    monkeypatch.setattr(
        te,
        "execute_post_checks",
        lambda **kwargs: (0, 0, 0),
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
    assert_that(code).is_equal_to(1)
