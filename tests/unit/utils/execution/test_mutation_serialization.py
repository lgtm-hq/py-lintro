"""The mutation phase never runs two mutating tools at once (#1743).

``lintro format`` rewrites files. Two mutating capabilities in flight over the
same file race on its bytes — each reads, rewrites and writes the whole file,
so the loser's edit is simply gone and the verify pass reports the difference
as an unexplained residual. Read-only actions have no such hazard and keep the
full fan-out, which is what the check-mode test here pins.

These run through the real ``AsyncToolExecutor`` rather than a double: the
property under test *is* concurrency, and a fake dispatcher would assert the
test's own loop instead of the production one.
"""

from __future__ import annotations

import threading
from typing import Any, cast

import pytest
from assertpy import assert_that

import lintro.utils.execution.parallel_executor as parallel_module
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.unified_config import UnifiedConfigManager

#: How long a tool waits for its batch mate to arrive. It has to outlast a
#: scheduler stall on a loaded runner, or a healthy concurrent dispatch would
#: read as a serialized one; it is paid as wall time only on the serialized
#: path, where waiting is the point.
_BARRIER_TIMEOUT_SECONDS: float = 10.0


class _Definition:
    """Definition stub exposing the one attribute the executor reads."""

    def __init__(self, name: str) -> None:
        """Record the tool name.

        Args:
            name: Registry key this double stands in for.
        """
        self.name = name


class _RecordingTool:
    """Tool double that records when its run starts and ends.

    Both ``fix`` and ``check`` block on a shared barrier. Under a concurrent
    dispatch every tool reaches the barrier and it releases; under a
    serialized one the first tool waits alone and the barrier breaks, which is
    what makes "these ran at the same time" a deterministic assertion rather
    than a sleep race.
    """

    def __init__(
        self,
        *,
        name: str,
        events: list[str],
        lock: threading.Lock,
        barrier: threading.Barrier,
    ) -> None:
        """Store the shared recording state.

        Args:
            name: Registry key this double stands in for.
            events: Shared, ordered log of start/end markers.
            lock: Guards ``events`` against interleaved appends.
            barrier: Released only when every tool is inside its run.
        """
        self.name = name
        self.definition = _Definition(name)
        self._events = events
        self._lock = lock
        self._barrier = barrier
        self.concurrent = False

    def _run(self, *, capability: str) -> ToolResult:
        """Record a start, wait for the other tools, then record an end.

        Args:
            capability: Which entry point the dispatcher routed to. Recorded
                in the event markers so an inverted route — ``check`` under a
                mutating action, or ``fix`` under a read-only one — fails the
                test instead of looking identical to the right one.

        Returns:
            ToolResult: A clean result for this tool.
        """
        with self._lock:
            self._events.append(f"start:{self.name}:{capability}")
        try:
            self._barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            self.concurrent = True
        except threading.BrokenBarrierError:
            # Nobody else was inside at the same time.
            self.concurrent = False
        with self._lock:
            self._events.append(f"end:{self.name}:{capability}")
        return ToolResult(
            name=self.name,
            success=True,
            output="ok",
            issues_count=0,
            initial_issues_count=0,
            fixed_issues_count=0,
            remaining_issues_count=0,
        )

    def fix(self, _paths: list[str], _options: dict[str, Any]) -> ToolResult:
        """Run the recording body as a mutating capability.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            ToolResult: A clean result for this tool.
        """
        return self._run(capability="fix")

    def check(self, _paths: list[str], _options: dict[str, Any]) -> ToolResult:
        """Run the recording body as a read-only capability.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            ToolResult: A clean result for this tool.
        """
        return self._run(capability="check")


def _dispatch(
    *,
    monkeypatch: pytest.MonkeyPatch,
    action: Action,
) -> tuple[list[str], dict[str, _RecordingTool]]:
    """Run two tools in one batch and return what they recorded.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        action: Action the executor dispatches.

    Returns:
        The ordered event log and the tool doubles, keyed by name.
    """
    events: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    tools = {
        name: _RecordingTool(
            name=name,
            events=events,
            lock=lock,
            barrier=barrier,
        )
        for name in ("ruff", "black")
    }
    monkeypatch.setattr(tool_manager, "get_tool", lambda name: tools[name])
    # One batch holding both tools: the batching itself is #1742's and is not
    # what this test is about.
    monkeypatch.setattr(
        tool_manager,
        "get_parallel_batches",
        lambda names: [list(names)],
    )
    monkeypatch.setattr(
        parallel_module,
        "configure_tool_for_execution",
        lambda **kwargs: kwargs["tool"],
    )

    results = run_tools_parallel(
        tools_to_run=["ruff", "black"],
        paths=["."],
        action=action,
        config_manager=cast(UnifiedConfigManager, object()),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        selected_tools={"ruff", "black"},
        max_workers=4,
    )

    assert_that([result.name for result in results]).contains("ruff", "black")
    return events, tools


def test_mutating_tools_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``fix`` batch runs one tool at a time, start to end.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    events, tools = _dispatch(monkeypatch=monkeypatch, action=Action.FIX)

    # Every start is immediately followed by its own end: no interleaving.
    assert_that(events).is_length(4)
    for index in range(0, len(events), 2):
        started = events[index].removeprefix("start:")
        ended = events[index + 1].removeprefix("end:")
        assert_that(events[index]).starts_with("start:")
        assert_that(events[index + 1]).starts_with("end:")
        assert_that(ended).is_equal_to(started)
    # The dispatcher routed to ``fix``: a batch serialized around ``check``
    # would satisfy the overlap assertions above and still be wrong.
    assert_that([event.endswith(":fix") for event in events]).is_equal_to(
        [True] * 4,
    )
    assert_that([tool.concurrent for tool in tools.values()]).is_equal_to(
        [False, False],
    )


def test_check_mode_still_runs_a_batch_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-only dispatch keeps the fan-out the serialization must not cost.

    The verify pass configures its tools with ``Action.CHECK``, so this is
    also the pin that serializing mutation did not serialize verification.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    events, tools = _dispatch(monkeypatch=monkeypatch, action=Action.CHECK)

    # Both tools were inside their run at the same instant: the barrier
    # released rather than breaking.
    assert_that([tool.concurrent for tool in tools.values()]).is_equal_to(
        [True, True],
    )
    assert_that(events[:2]).contains("start:ruff:check", "start:black:check")
