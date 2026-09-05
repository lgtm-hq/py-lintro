"""Integration tests for parallel tool execution.

This module has two parts:

* Single-tool smoke tests exercise ``run_lint_tools_simple`` end to end but
  intentionally stay on the sequential code path (one tool per invocation).
  They are named ``*_smoke`` so their limited scope is honest.
* Multi-tool parallel tests drive the production gate
  (``use_parallel`` in :mod:`lintro.utils.tool_executor`) and the real
  parallel executor over mixed-language samples.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.plugins import ToolRegistry
from lintro.utils import tool_executor as tool_executor_mod
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.tool_executor import run_lint_tools_simple
from lintro.utils.unified_config import UnifiedConfigManager
from tests.integration._tools import require_tool


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
    inconsistent mapping-value spacing (yamllint), so ruff and yamllint each
    own exactly one file and each report at least one issue. This lets
    multi-tool tests assert real cross-tool aggregation.

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


# Both multi-tool tests below drive the real ruff and yamllint binaries.
_requires_ruff = require_tool("ruff")
_requires_yamllint = require_tool("yamllint")


@_requires_ruff
@_requires_yamllint
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


@_requires_ruff
@_requires_yamllint
def test_simple_runner_uses_parallel_for_multiple_tools(
    mixed_language_sample: list[str],
) -> None:
    """The production simple runner takes the parallel path for two tools.

    ``run_lint_tools_simple`` is the CLI entry used by ``lintro check``. Passing
    two tools must call ``_execute_tools_parallel`` so a regression that forces
    sequential execution is visible here.

    Args:
        mixed_language_sample: Paths ``[python_file, yaml_file]``.
    """
    with patch.object(
        target=tool_executor_mod,
        attribute="_execute_tools_parallel",
        wraps=tool_executor_mod._execute_tools_parallel,
    ) as execute_parallel:
        exit_code = run_lint_tools_simple(
            action="check",
            paths=mixed_language_sample,
            tools="ruff,yamllint",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="file",
            output_format="grid",
            verbose=False,
            yes=True,
        )

    assert_that(execute_parallel.call_count).is_equal_to(1)
    assert_that(exit_code).is_instance_of(int)
    assert_that(exit_code).is_not_equal_to(0)
