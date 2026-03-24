"""Unit tests for executor post-check behavior (e.g., Black as post-check)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    pass

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
        self.options: dict[str, object] = {}

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


def _stub_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    import lintro.utils.console as cl

    class SilentLogger:
        def __getattr__(self, name: str) -> Callable[..., None]:
            def _(*a: Any, **k: Any) -> None:
                return None

            return _

    monkeypatch.setattr(cl, "create_logger", lambda *_a, **_k: SilentLogger())


def _setup_main_tool(monkeypatch: pytest.MonkeyPatch) -> FakeTool:
    """Configure the main (ruff) tool and output manager stubs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        FakeTool: Configured ruff tool stub.
    """
    import lintro.utils.tool_executor as te

    ok = ToolResult(name="ruff", success=True, output="", issues_count=0)
    ruff = FakeTool("ruff", can_fix=True, result=ok)

    def fake_get_tools(
        tools: str | None,
        action: str,
        **_kwargs: object,
    ) -> ToolsToRunResult:
        return ToolsToRunResult(to_run=["ruff"])

    monkeypatch.setattr(te, "get_tools_to_run", fake_get_tools, raising=True)
    monkeypatch.setattr(tool_manager, "get_tool", lambda name: ruff, raising=True)

    def noop_write_reports_from_results(
        self: object,
        results: list[ToolResult],
    ) -> None:
        return None

    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        noop_write_reports_from_results,
        raising=True,
    )

    return ruff


def test_post_checks_missing_tool_is_skipped_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When post-check tool is unavailable, it is skipped gracefully.

    Post-checks are optional when the tool cannot be resolved from the tool
    manager. The main tool (ruff) should run, and the missing post-check tool
    (black) should not appear in results.

    Args:
        monkeypatch: Pytest fixture to modify objects during the test.
        capsys: Pytest fixture to capture stdout/stderr for assertions.
    """
    _stub_logger(monkeypatch)
    ruff_local = _setup_main_tool(monkeypatch)

    import lintro.utils.config as cfg
    import lintro.utils.post_checks as pc
    import lintro.utils.tool_executor as te

    def post_check_config():
        return {"enabled": True, "tools": ["black"], "enforce_failure": True}

    # Patch in all modules that import load_post_checks_config
    monkeypatch.setattr(cfg, "load_post_checks_config", post_check_config, raising=True)
    monkeypatch.setattr(te, "load_post_checks_config", post_check_config, raising=True)
    monkeypatch.setattr(pc, "load_post_checks_config", post_check_config, raising=True)

    # Fail only for the post-check tool (black); allow main ruff to run
    def fail_get_tool(name: str) -> FakeTool:
        if name == "black":
            raise RuntimeError("black not available")
        return ruff_local

    monkeypatch.setattr(tool_manager, "get_tool", fail_get_tool, raising=True)

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
    results = data.get("results", [])
    tool_names = [result["tool"] for result in results]
    # Main tool (ruff) should run successfully
    assert_that("ruff" in tool_names).is_true()
    # Post-check (black) should be skipped, not appear in results
    assert_that("black" in tool_names).is_false()
    # Exit code should be success (0) since main tool passed and post-check was skipped
    assert_that(code).is_equal_to(0)
