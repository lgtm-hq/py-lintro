"""Concurrency regression tests for post-check tool isolation (issue #1116).

The post-check execution path (``execute_post_checks``) fetches a tool from
the registry singleton via ``tool_manager.get_tool`` and previously configured
and ran it in place, mutating shared option state. Under concurrent runs with
different options this reintroduces the shared-state race that #1080 fixed on
the primary sequential/parallel paths.

These tests verify the fix: the singleton is used only as a lookup template,
while every mutation and execution targets a per-invocation isolated copy from
``copy_for_execution()``. No option/state bleeds across concurrent runs and the
singleton's options remain unchanged afterward.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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


@dataclass
class _ObservingPostCheckPlugin(BaseToolPlugin):
    """Post-check plugin whose check() records the options it observes.

    ``check`` reads its option markers, yields the GIL via a short sleep, then
    reads them again. If two concurrent invocations shared one instance, the
    second read could observe the other invocation's options (a clobber).
    """

    _definition: ToolDefinition = field(
        default_factory=lambda: ToolDefinition(
            name="observing-post-check",
            description="Observes options during post-check",
            file_patterns=["*.py"],
            can_fix=False,
            default_timeout=30,
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
        """Read the marker options before and after yielding the GIL.

        Args:
            paths: Unused input paths.
            options: Unused runtime options (config is applied via set_options).

        Returns:
            ToolResult whose output encodes the observed options, and whose
            issues_count is 1 if any observed option changed mid-call (a
            clobber from a concurrent invocation), else 0.
        """
        before_venv = self.include_venv
        before_excl = tuple(self.exclude_patterns)
        time.sleep(0.02)
        after_venv = self.include_venv
        after_excl = tuple(self.exclude_patterns)
        clobbered = 0 if (before_venv, before_excl) == (after_venv, after_excl) else 1
        # Encode only the invocation-specific tokens: the last exclude pattern
        # (the case marker, since defaults/.lintro-ignore are prepended) plus
        # the observed include_venv flag.
        marker = after_excl[-1] if after_excl else ""
        return ToolResult(
            name=self.definition.name,
            success=clobbered == 0,
            output=f"{after_venv}:{marker}",
            issues_count=clobbered,
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


class _NoopConfigManager:
    """UnifiedConfigManager stub that leaves the tool untouched."""

    def apply_config_to_tool(self, *, tool: object) -> None:
        """No-op config application.

        Args:
            tool: The tool that would be configured (left untouched).
        """
        return None


@pytest.fixture
def observing_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> _ObservingPostCheckPlugin:
    """Register an observing plugin as the post-check registry singleton.

    Wires ``tool_manager.get_tool`` to return the shared template, marks it as
    registered, stubs the config manager to a no-op, and silences logging so
    ``execute_post_checks`` exercises only the isolation behavior under test.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The shared singleton template instance.
    """
    template = _ObservingPostCheckPlugin()

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
        "UnifiedConfigManager",
        _NoopConfigManager,
        raising=True,
    )
    monkeypatch.setattr(
        pc,
        "load_post_checks_config",
        lambda: {
            "enabled": True,
            "tools": ["observing-post-check"],
            "enforce_failure": False,
        },
        raising=True,
    )
    return template


def _run_post_check(*, include_venv: bool, exclude: str) -> ToolResult:
    """Drive ``execute_post_checks`` once and return the post-check result.

    Args:
        include_venv: Value threaded into the post-check tool.
        exclude: Comma-separated exclude patterns threaded into the tool.

    Returns:
        The single post-check ToolResult appended during execution.
    """
    results: list[ToolResult] = []
    pc.execute_post_checks(
        action=Action.CHECK,
        paths=["."],
        exclude=exclude,
        include_venv=include_venv,
        group_by="auto",
        output_format="grid",
        verbose=False,
        raw_output=False,
        logger=cast("ThreadSafeConsoleLogger", _SilentLogger()),
        all_results=results,
        total_issues=0,
        total_fixed=0,
        total_remaining=0,
    )
    # The observing post-check tool always appends exactly one result.
    return next(r for r in results if r.name == "observing-post-check")


def test_post_check_uses_isolated_copy_not_singleton(
    observing_singleton: _ObservingPostCheckPlugin,
) -> None:
    """A single post-check run must not mutate the registry singleton."""
    baseline_excludes = list(observing_singleton.exclude_patterns)

    result = _run_post_check(include_venv=True, exclude="unique_marker_*")

    # The run observed its own options (include_venv + its case marker) on the
    # isolated copy, with no mid-call clobber.
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_equal_to("True:unique_marker_*")

    # The singleton template was never mutated by the run: its include_venv is
    # unchanged and its exclude patterns never gained the invocation's marker.
    assert_that(observing_singleton.include_venv).is_false()
    assert_that(observing_singleton.exclude_patterns).is_equal_to(baseline_excludes)
    assert_that(observing_singleton.exclude_patterns).does_not_contain(
        "unique_marker_*",
    )


def test_concurrent_post_checks_do_not_bleed(
    observing_singleton: _ObservingPostCheckPlugin,
) -> None:
    """Two concurrent post-check runs each see only their own options.

    Runs many ``execute_post_checks`` invocations concurrently, each with a
    distinct include_venv/exclude combination. No invocation may observe a
    clobbered (interleaved) option, each must read back its own values, and the
    shared singleton's options must be unchanged afterward.
    """
    baseline_excludes = list(observing_singleton.exclude_patterns)
    cases = [{"include_venv": bool(i % 2), "exclude": f"case{i}_*"} for i in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda c: _run_post_check(
                    include_venv=c["include_venv"],
                    exclude=c["exclude"],
                ),
                cases,
            ),
        )

    # No invocation observed a mid-call clobber from a concurrent run.
    assert_that([r.issues_count for r in results]).does_not_contain(1)

    # Each invocation observed exactly its own options (venv flag + marker).
    observed = sorted(r.output or "" for r in results)
    expected = sorted(f"{c['include_venv']}:{c['exclude']}" for c in cases)
    assert_that(observed).is_equal_to(expected)

    # The registry singleton's options are unchanged afterward: include_venv
    # stays False and no invocation's case marker leaked onto the template.
    assert_that(observing_singleton.include_venv).is_false()
    assert_that(observing_singleton.exclude_patterns).is_equal_to(baseline_excludes)
