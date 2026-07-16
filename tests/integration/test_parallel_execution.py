"""Integration tests for parallel tool execution.

This module has two parts:

* Single-tool smoke tests exercise ``run_lint_tools_simple`` end to end but
  intentionally stay on the sequential code path (one tool per invocation).
  They are named ``*_smoke`` so their limited scope is honest.
* Multi-tool parallel tests drive the real parallel executor
  (:func:`lintro.utils.execution.parallel_executor.run_tools_parallel` and
  :class:`lintro.utils.async_tool_executor.AsyncToolExecutor`) with two or more
  tools over mixed-language samples and assert result aggregation, the ordering
  contract, failure isolation, and conflict-aware batching.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry
from lintro.utils.async_tool_executor import (
    AsyncToolExecutor,
    get_parallel_batches,
)
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.tool_executor import run_lint_tools_simple
from lintro.utils.unified_config import UnifiedConfigManager


@pytest.fixture(autouse=True)
def set_lintro_test_mode_env(lintro_test_mode: object) -> Iterator[None]:
    """Set test mode for all tests in this module.

    Args:
        lintro_test_mode: Shared fixture that manages env vars.

    Yields:
        None: This fixture is used for its side effect only.
    """
    yield


@pytest.fixture
def temp_python_files() -> Iterator[list[str]]:
    """Create multiple temporary Python files for parallel testing.

    Yields:
        list[str]: List of paths to temporary Python files.
    """
    files: list[str] = []
    temp_dir = tempfile.mkdtemp()

    # Create multiple files with various issues
    file_contents = [
        (
            "file1.py",
            "import sys\nimport os\n\ndef add(a, b):\n    return a + b\n",
        ),
        (
            "file2.py",
            "def greet(name: str) -> str:\n    return f'Hello, {name}!'\n",
        ),
        (
            "file3.py",
            "import json\n\ndata = {'key': 'value'}\n",
        ),
    ]

    for filename, content in file_contents:
        file_path = os.path.join(temp_dir, filename)
        with open(file_path, "w") as f:
            f.write(content)
        files.append(file_path)

    yield files

    # Cleanup
    for file_path in files:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(file_path)
    with contextlib.suppress(OSError):
        os.rmdir(temp_dir)


@pytest.fixture
def mixed_language_sample() -> Iterator[list[str]]:
    """Create a mixed-language sample with one violation per tool.

    The Python file has an unused import (ruff ``F401``) and the YAML file has
    inconsistent spacing plus a missing document start (yamllint), so ruff and
    yamllint each own exactly one file and each report at least one issue. This
    lets multi-tool tests assert real cross-tool aggregation.

    Yields:
        list[str]: Paths ``[python_file, yaml_file]``.
    """
    temp_dir = tempfile.mkdtemp()
    py_path = os.path.join(temp_dir, "bad.py")
    yaml_path = os.path.join(temp_dir, "bad.yaml")

    with open(py_path, "w") as f:
        f.write("import os\n\n\ndef add(a, b):\n    return a + b\n")
    with open(yaml_path, "w") as f:
        f.write("a: 1\nb: 2\nc:  3\n")

    paths = [py_path, yaml_path]
    yield paths

    for file_path in paths:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(file_path)
    with contextlib.suppress(OSError):
        os.rmdir(temp_dir)


# ---------------------------------------------------------------------------
# Fakes for failure-isolation and batching contracts
# ---------------------------------------------------------------------------


@dataclass
class _FakeDefinition:
    """Minimal stand-in for a tool definition.

    Attributes:
        name: Tool name.
        conflicts_with: Names of tools this one conflicts with.
    """

    name: str
    conflicts_with: list[str] = field(default_factory=list)


@dataclass
class _FakeTool:
    """Minimal tool double used to drive the async executor deterministically.

    Attributes:
        name: Tool name (mirrored into the fake definition).
        conflicts_with: Conflicting tool names for batching tests.
        sleep_for: Seconds to block inside ``check`` to control completion order.
        raises: Whether ``check`` should raise to test failure isolation.
        issues_count: Issue count reported by the returned result on success.
    """

    name: str
    conflicts_with: list[str] = field(default_factory=list)
    sleep_for: float = 0.0
    raises: bool = False
    issues_count: int = 0

    def __post_init__(self) -> None:
        """Build the fake definition from the tool name."""
        self.definition = _FakeDefinition(
            name=self.name,
            conflicts_with=self.conflicts_with,
        )

    def check(
        self,
        paths: list[str],
        options: dict[str, Any],
    ) -> ToolResult:
        """Simulate a tool check run.

        Args:
            paths: Paths passed by the executor (unused).
            options: Options passed by the executor (unused).

        Returns:
            ToolResult: A success result after an optional delay.

        Raises:
            RuntimeError: When ``raises`` is set, to test isolation.
        """
        if self.sleep_for:
            time.sleep(self.sleep_for)
        if self.raises:
            raise RuntimeError(f"{self.name} boom")
        return ToolResult(
            name=self.name,
            success=True,
            output=f"{self.name} ok",
            issues_count=self.issues_count,
        )


@dataclass
class _FakeToolManager:
    """Tool manager double returning fake tools for batching tests.

    Attributes:
        tools: Mapping of tool name to fake tool instance.
    """

    tools: dict[str, _FakeTool]

    def get_tool(self, name: str) -> _FakeTool:
        """Return the fake tool registered under ``name``.

        Args:
            name: Tool name to look up.

        Returns:
            _FakeTool: The registered fake tool.
        """
        return self.tools[name]


# ---------------------------------------------------------------------------
# Single-tool smoke tests (sequential path — one tool per invocation)
# ---------------------------------------------------------------------------


def test_check_multiple_files_smoke(temp_python_files: list[str]) -> None:
    """Smoke check on multiple files with a single tool.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code = run_lint_tools_simple(
        action="check",
        paths=temp_python_files,
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=False,
        raw_output=False,
    )

    # Should complete without crashing
    assert_that(exit_code).is_instance_of(int)


def test_consistent_results_across_runs_smoke(temp_python_files: list[str]) -> None:
    """Smoke test that repeated single-tool runs are consistent.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    # Run twice
    exit_code_1 = run_lint_tools_simple(
        action="check",
        paths=temp_python_files,
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=False,
    )

    exit_code_2 = run_lint_tools_simple(
        action="check",
        paths=temp_python_files,
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=False,
    )

    # Exit codes should match
    assert_that(exit_code_1).is_equal_to(exit_code_2)


def test_check_with_single_file_smoke(temp_python_files: list[str]) -> None:
    """Smoke check with a single file and single tool.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code = run_lint_tools_simple(
        action="check",
        paths=[temp_python_files[0]],
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=False,
    )

    assert_that(exit_code).is_instance_of(int)


def test_format_action_smoke(temp_python_files: list[str]) -> None:
    """Smoke test of the format action with a single tool.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code = run_lint_tools_simple(
        action="fmt",
        paths=temp_python_files,
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format="grid",
        verbose=False,
    )

    assert_that(exit_code).is_instance_of(int)


def test_different_output_formats_smoke(temp_python_files: list[str]) -> None:
    """Smoke test of different output formats with a single tool.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    for fmt in ["grid", "plain", "json"]:
        exit_code = run_lint_tools_simple(
            action="check",
            paths=temp_python_files,
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format=fmt,
            verbose=False,
        )
        assert_that(exit_code).is_instance_of(int)


def test_tool_definition_exists() -> None:
    """Test that ruff tool has proper definition."""
    ruff_tool = ToolRegistry.get("ruff")

    assert_that(ruff_tool).is_not_none()
    assert_that(ruff_tool.definition).is_not_none()
    assert_that(ruff_tool.definition.name).is_equal_to("ruff")


def test_tool_respects_execution_order_smoke(temp_python_files: list[str]) -> None:
    """Smoke test that single-tool exit codes are stable across runs.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    # Run multiple times to verify consistency
    results = []
    for _ in range(3):
        exit_code = run_lint_tools_simple(
            action="check",
            paths=temp_python_files,
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="grid",
            verbose=False,
        )
        results.append(exit_code)

    # All runs should produce same exit code
    assert_that(len(set(results))).is_equal_to(1)


# ---------------------------------------------------------------------------
# Multi-tool parallel tests (real parallel executor)
# ---------------------------------------------------------------------------


def _require_tools(*names: str) -> None:
    """Skip the current test if any required tool is missing from PATH.

    Args:
        *names: Executable names that must resolve on PATH.
    """
    import shutil

    for name in names:
        if not shutil.which(name):
            pytest.skip(f"Tool '{name}' not available in PATH")


def test_parallel_runs_multiple_tools_over_mixed_samples(
    mixed_language_sample: list[str],
) -> None:
    """Two tools run concurrently and both report their own issues.

    Drives the real parallel executor with ruff + yamllint over a mixed sample
    where each tool owns exactly one file, and asserts that results for both
    tools are aggregated and that each surfaces its violation.

    Args:
        mixed_language_sample: Paths ``[python_file, yaml_file]``.
    """
    _require_tools("ruff", "yamllint")

    results = run_tools_parallel(
        tools_to_run=["ruff", "yamllint"],
        paths=mixed_language_sample,
        action=Action.CHECK,
        config_manager=UnifiedConfigManager(),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        post_tools=set(),
        max_workers=4,
    )

    names = {result.name for result in results}
    assert_that(names).is_equal_to({"ruff", "yamllint"})

    by_name = {result.name: result for result in results}
    # Aggregation: each tool contributes its own findings independently.
    assert_that(by_name["ruff"].issues_count).is_greater_than_or_equal_to(1)
    assert_that(by_name["yamllint"].issues_count).is_greater_than_or_equal_to(1)
    assert_that(by_name["ruff"].success).is_false()
    assert_that(by_name["yamllint"].success).is_false()


def test_parallel_preserves_input_ordering_contract() -> None:
    """Result order matches input tool order regardless of completion time.

    The first tool sleeps longer than the second, so it finishes last, but the
    executor must still return results positionally aligned with the input
    tool list (the ordering contract callers rely on for display).
    """
    slow = _FakeTool(name="slow", sleep_for=0.20, issues_count=1)
    fast = _FakeTool(name="fast", sleep_for=0.0, issues_count=2)

    with AsyncToolExecutor(max_workers=4) as executor:
        results = asyncio.run(
            executor.run_tools_parallel(
                tools=[("slow", slow), ("fast", fast)],
                paths=[],
                action=Action.CHECK,
            ),
        )

    ordered_names = [name for name, _ in results]
    assert_that(ordered_names).is_equal_to(["slow", "fast"])
    assert_that(results[0][1].issues_count).is_equal_to(1)
    assert_that(results[1][1].issues_count).is_equal_to(2)


def test_parallel_isolates_tool_failures() -> None:
    """A tool raising an exception does not sink its concurrent peers.

    One fake tool raises inside ``check``; the executor must convert it to a
    failed :class:`ToolResult` while the healthy tool still returns its own
    successful result, and ordering must be preserved.
    """
    boom = _FakeTool(name="boom", raises=True)
    healthy = _FakeTool(name="healthy", issues_count=3)

    with AsyncToolExecutor(max_workers=4) as executor:
        results = asyncio.run(
            executor.run_tools_parallel(
                tools=[("boom", boom), ("healthy", healthy)],
                paths=[],
                action=Action.CHECK,
            ),
        )

    by_name = {name: result for name, result in results}
    assert_that(sorted(by_name.keys())).is_equal_to(["boom", "healthy"])

    # Failing tool is isolated into a failed result, not propagated.
    assert_that(by_name["boom"].success).is_false()
    assert_that(by_name["boom"].output).contains("boom boom")

    # Healthy tool is unaffected by its peer's failure.
    assert_that(by_name["healthy"].success).is_true()
    assert_that(by_name["healthy"].issues_count).is_equal_to(3)


def test_get_parallel_batches_separates_conflicting_tools() -> None:
    """Conflicting tools are placed in distinct sequential batches.

    Two tools that declare ``conflicts_with`` each other must never share a
    batch (they would race on the same files), while a third independent tool
    may share a batch with one of them.
    """
    manager = _FakeToolManager(
        tools={
            "black": _FakeTool(name="black", conflicts_with=["ruff"]),
            "ruff": _FakeTool(name="ruff", conflicts_with=["black"]),
            "yamllint": _FakeTool(name="yamllint"),
        },
    )

    batches = get_parallel_batches(["black", "ruff", "yamllint"], manager)

    # black and ruff conflict -> separate batches; two batches total.
    assert_that(batches).is_length(2)
    for batch in batches:
        conflicting_together = "black" in batch and "ruff" in batch
        assert_that(conflicting_together).is_false()

    # Every input tool is scheduled exactly once.
    scheduled = [tool for batch in batches for tool in batch]
    assert_that(sorted(scheduled)).is_equal_to(["black", "ruff", "yamllint"])


def test_get_parallel_batches_groups_independent_tools() -> None:
    """Non-conflicting tools share a single parallel batch."""
    manager = _FakeToolManager(
        tools={
            "ruff": _FakeTool(name="ruff"),
            "yamllint": _FakeTool(name="yamllint"),
        },
    )

    batches = get_parallel_batches(["ruff", "yamllint"], manager)

    assert_that(batches).is_length(1)
    assert_that(sorted(batches[0])).is_equal_to(["ruff", "yamllint"])
