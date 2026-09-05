"""Regression tests for ``--tool-options`` reaching post-check tools.

``execute_post_checks`` configured its post-check tool with
``UnifiedConfigManager.apply_config_to_tool(tool=tool)`` and never passed the
parsed ``--tool-options`` mapping, so per-tool CLI overrides were silently
dropped on that path. With ``[tool.lintro.post_checks] tools = ["black"]``,
``lintro chk --tool-options black:timeout=120`` still ran Black on the 30s
default and failed with "Black execution timed out (30.0s limit exceeded)".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from assertpy import assert_that

import lintro.utils.post_checks as pc
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import ToolRegistry
from lintro.tools import tool_manager
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from lintro.utils.tool_options import parse_tool_options

_POST_CHECK_TOOL_NAME = "timeout-observing-post-check"
_DEFAULT_TIMEOUT = 30


@dataclass
class _TimeoutObservingPlugin(BaseToolPlugin):
    """Post-check plugin that reports the timeout it would execute with."""

    _definition: ToolDefinition = field(
        default_factory=lambda: ToolDefinition(
            name=_POST_CHECK_TOOL_NAME,
            description="Reports its effective timeout",
            file_patterns=["*.py"],
            can_fix=False,
            default_timeout=_DEFAULT_TIMEOUT,
            default_options={"timeout": _DEFAULT_TIMEOUT},
        ),
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            The tool definition.
        """
        return self._definition

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Report the timeout resolved from the plugin's options.

        Args:
            paths: Unused input paths.
            options: Unused runtime options.

        Returns:
            ToolResult whose output is the observed timeout value.
        """
        timeout = self.options.get("timeout", _DEFAULT_TIMEOUT)
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=str(timeout),
            issues_count=0,
        )


class _SilentLogger:
    """No-op logger stub swallowing all console calls."""

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Return a no-op for any attribute access.

        Args:
            name: Attribute name being looked up.

        Returns:
            A callable that ignores all arguments.
        """

        def _(*_a: Any, **_k: Any) -> None:
            return None

        return _


@pytest.fixture
def observing_post_check(
    monkeypatch: pytest.MonkeyPatch,
) -> _TimeoutObservingPlugin:
    """Register the timeout-observing plugin as the only post-check tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The registered plugin template instance.
    """
    template = _TimeoutObservingPlugin()
    monkeypatch.setattr(
        tool_manager,
        "get_tool",
        lambda name: template,
        raising=True,
    )
    monkeypatch.setattr(
        ToolRegistry,
        "is_registered",
        staticmethod(lambda name: True),
        raising=True,
    )
    monkeypatch.setattr(
        pc,
        "load_post_checks_config",
        lambda: {
            "enabled": True,
            "tools": [_POST_CHECK_TOOL_NAME],
            "enforce_failure": False,
        },
        raising=True,
    )
    return template


def _run_post_check(
    *,
    tool_option_dict: dict[str, dict[str, object]] | None,
) -> ToolResult:
    """Drive ``execute_post_checks`` once and return the post-check result.

    Args:
        tool_option_dict: Parsed ``--tool-options`` mapping to thread through.

    Returns:
        The single post-check ToolResult appended during execution.
    """
    results: list[ToolResult] = []
    pc.execute_post_checks(
        action=Action.CHECK,
        paths=["."],
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
        logger=cast("ThreadSafeConsoleLogger", _SilentLogger()),
        all_results=results,
        total_issues=0,
        total_fixed=0,
        total_remaining=0,
        tool_option_dict=tool_option_dict,
    )
    return next(r for r in results if r.name == _POST_CHECK_TOOL_NAME)


def test_post_check_honours_tool_options_timeout(
    observing_post_check: _TimeoutObservingPlugin,
) -> None:
    """A per-tool ``timeout`` override must reach the post-check tool."""
    tool_option_dict = parse_tool_options(f"{_POST_CHECK_TOOL_NAME}:timeout=120")

    result = _run_post_check(tool_option_dict=tool_option_dict)

    assert_that(result.output).is_equal_to("120.0")


def test_post_check_without_tool_options_keeps_default_timeout(
    observing_post_check: _TimeoutObservingPlugin,
) -> None:
    """Without CLI overrides the post-check tool keeps its default timeout."""
    result = _run_post_check(tool_option_dict=None)

    assert_that(result.output).is_equal_to(str(_DEFAULT_TIMEOUT))
