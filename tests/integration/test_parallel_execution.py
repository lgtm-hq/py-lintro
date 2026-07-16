"""Integration tests for parallel tool execution.

Single-tool smoke tests below exercise the sequential path only
(``use_parallel`` requires ``len(tools_to_run) > 1``). Multi-tool tests
exercise the asyncio + ThreadPoolExecutor path and conflict-aware batching.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.plugins import ToolRegistry
from lintro.tools import tool_manager
from lintro.utils.async_tool_executor import get_parallel_batches
from lintro.utils.tool_executor import run_lint_tools_simple

_requires_ruff = pytest.mark.skipif(
    shutil.which("ruff") is None,
    reason="ruff not installed",
)


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
    """Create multiple temporary Python files for single-tool smoke tests.

    Yields:
        list[str]: List of paths to temporary Python files.
    """
    files: list[str] = []
    temp_dir = tempfile.mkdtemp()

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

    for file_path in files:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(file_path)
    with contextlib.suppress(OSError):
        os.rmdir(temp_dir)


@pytest.fixture
def multi_tool_fixture_dir(tmp_path: Path) -> Path:
    """Create a fixture directory with one ruff and one yamllint violation.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the fixture directory containing ``bad.py`` and ``bad.yaml``.
    """
    (tmp_path / "bad.py").write_text("import os\n")
    # document-start present; trailing spaces is the sole yamllint finding
    (tmp_path / "bad.yaml").write_text("---\nname: test   \n")
    return tmp_path


@pytest.fixture
def disable_post_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable post-checks so only explicitly selected tools appear in results.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import lintro.utils.post_checks as post_checks
    import lintro.utils.tool_executor as tool_executor

    empty_post: dict[str, object] = {"enabled": False, "tools": []}
    monkeypatch.setattr(
        tool_executor,
        "load_post_checks_config",
        lambda: empty_post,
    )
    monkeypatch.setattr(
        post_checks,
        "load_post_checks_config",
        lambda: empty_post,
    )


def _run_check(
    *,
    paths: list[str],
    tools: str,
    output_format: str = "grid",
    output_file: str | None = None,
) -> int:
    """Run a check via ``run_lint_tools_simple`` with shared defaults.

    Args:
        paths: Paths to lint.
        tools: Comma-separated tool names.
        output_format: Output format string.
        output_file: Optional path for structured output.

    Returns:
        Process exit code.

    Raises:
        TypeError: If ``run_lint_tools_simple`` does not return an int.
    """
    exit_code = run_lint_tools_simple(
        action="check",
        paths=paths,
        tools=tools,
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="file",
        output_format=output_format,
        verbose=False,
        raw_output=False,
        output_file=output_file,
        yes=True,
    )
    # Narrow for dogfooding mypy, which type-checks this file in isolation and
    # otherwise treats run_lint_tools_simple as returning Any (no-any-return).
    if not isinstance(exit_code, int):
        raise TypeError(f"expected int exit code, got {type(exit_code)!r}")
    return exit_code


# =============================================================================
# Single-tool smoke tests (sequential path only — not true parallelism)
# =============================================================================


@_requires_ruff
def test_single_tool_check_multiple_files(temp_python_files: list[str]) -> None:
    """Smoke: single-tool check on multiple files completes without crashing.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code = _run_check(paths=temp_python_files, tools="ruff")
    assert_that(exit_code).is_instance_of(int)


@_requires_ruff
def test_single_tool_consistent_results_across_runs(
    temp_python_files: list[str],
) -> None:
    """Smoke: repeated single-tool runs produce the same exit code.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code_1 = _run_check(paths=temp_python_files, tools="ruff")
    exit_code_2 = _run_check(paths=temp_python_files, tools="ruff")
    assert_that(exit_code_1).is_equal_to(exit_code_2)


@_requires_ruff
def test_single_tool_check_with_one_file(temp_python_files: list[str]) -> None:
    """Smoke: single-tool check on one file completes without crashing.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    exit_code = _run_check(paths=[temp_python_files[0]], tools="ruff")
    assert_that(exit_code).is_instance_of(int)


@_requires_ruff
def test_single_tool_format_action(temp_python_files: list[str]) -> None:
    """Smoke: single-tool format action completes without crashing.

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
        yes=True,
    )
    assert_that(exit_code).is_instance_of(int)


@_requires_ruff
def test_single_tool_different_output_formats(temp_python_files: list[str]) -> None:
    """Smoke: single-tool check works across output formats.

    Args:
        temp_python_files: Pytest fixture providing temp files.
    """
    for fmt in ["grid", "plain", "json"]:
        exit_code = _run_check(
            paths=temp_python_files,
            tools="ruff",
            output_format=fmt,
        )
        assert_that(exit_code).is_instance_of(int)


def test_ruff_tool_definition_exists() -> None:
    """Smoke: ruff tool is registered with a valid definition."""
    ruff_tool = ToolRegistry.get("ruff")

    assert_that(ruff_tool).is_not_none()
    assert_that(ruff_tool.definition).is_not_none()
    assert_that(ruff_tool.definition.name).is_equal_to("ruff")


# =============================================================================
# Multi-tool parallel execution
# =============================================================================


@_requires_ruff
def test_parallel_check_runs_multiple_non_conflicting_tools(
    multi_tool_fixture_dir: Path,
    disable_post_checks: None,
    skip_if_tool_unavailable: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run ruff + yamllint in parallel and assert both report issues.

    Args:
        multi_tool_fixture_dir: Fixture dir with one violation per tool.
        disable_post_checks: Ensures only selected tools appear in results.
        skip_if_tool_unavailable: Skip helper when a binary is missing.
        monkeypatch: Used to spy that the parallel executor path was entered.
    """
    skip_if_tool_unavailable("yamllint")

    import lintro.utils.tool_executor as tool_executor

    parallel_calls: list[list[str]] = []
    original_parallel = tool_executor.run_tools_parallel

    def _spy_run_tools_parallel(
        *args: object,
        **kwargs: object,
    ) -> object:
        tools_arg = kwargs.get("tools_to_run", args[0] if args else None)
        if not isinstance(tools_arg, list):
            raise TypeError(
                f"expected tools_to_run list, got {type(tools_arg)!r}",
            )
        parallel_calls.append([str(name) for name in tools_arg])
        return original_parallel(*args, **kwargs)

    monkeypatch.setattr(
        tool_executor,
        "run_tools_parallel",
        _spy_run_tools_parallel,
    )

    output_path = multi_tool_fixture_dir / "results.json"
    exit_code = _run_check(
        paths=[str(multi_tool_fixture_dir)],
        tools="ruff,yamllint",
        output_format="json",
        output_file=str(output_path),
    )

    assert_that(parallel_calls).is_length(1)
    assert_that(parallel_calls[0]).contains("ruff", "yamllint")
    assert_that(len(parallel_calls[0])).is_greater_than_or_equal_to(2)

    assert_that(exit_code).is_not_equal_to(0)
    assert_that(output_path.exists()).is_true()

    raw_output = output_path.read_text()
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"output_file was not valid JSON: {exc}; body={raw_output[:200]!r}",
        ) from exc

    assert_that(payload).contains_key("results")
    assert_that(payload["results"]).is_instance_of(list)
    assert_that(payload["results"]).is_not_empty()

    results_by_tool = {
        item["tool"]: item
        for item in payload["results"]
        if isinstance(item, dict) and "tool" in item
    }

    assert_that(results_by_tool).contains_key("ruff")
    assert_that(results_by_tool).contains_key("yamllint")
    assert_that(results_by_tool["ruff"]).contains_key("issues_count")
    assert_that(results_by_tool["yamllint"]).contains_key("issues_count")
    assert_that(results_by_tool["ruff"]["issues_count"]).is_greater_than_or_equal_to(1)
    assert_that(
        results_by_tool["yamllint"]["issues_count"],
    ).is_greater_than_or_equal_to(1)
    assert_that(results_by_tool["ruff"].get("issues")).is_not_empty()
    assert_that(results_by_tool["yamllint"].get("issues")).is_not_empty()


def test_non_conflicting_tools_share_one_parallel_batch() -> None:
    """Real ruff + yamllint definitions have no conflicts → one batch."""
    batches = get_parallel_batches(["ruff", "yamllint"], tool_manager)

    assert_that(batches).is_length(1)
    assert_that(batches[0]).contains("ruff", "yamllint")


# =============================================================================
# Conflict-aware batching
# =============================================================================


def test_conflicting_tools_are_batched_separately() -> None:
    """Tools whose definitions declare conflicts_with land in separate batches.

    No shipped tools currently declare conflicts, so this builds definitions
    from the live registry (black/ruff) with synthetic ``conflicts_with`` via
    ``dataclasses.replace``.
    """
    black_def = replace(
        tool_manager.get_tool("black").definition,
        conflicts_with=["ruff"],
    )
    ruff_def = replace(
        tool_manager.get_tool("ruff").definition,
        conflicts_with=["black"],
    )
    mypy_def = tool_manager.get_tool("mypy").definition

    definitions = {
        "black": black_def,
        "ruff": ruff_def,
        "mypy": mypy_def,
    }
    manager = MagicMock()
    manager.get_tool.side_effect = lambda name: SimpleNamespace(
        definition=definitions[name],
    )

    batches = get_parallel_batches(["black", "ruff", "mypy"], manager)

    assert_that(len(batches)).is_greater_than_or_equal_to(2)

    black_batch_idx: int | None = None
    ruff_batch_idx: int | None = None
    for index, batch in enumerate(batches):
        if "black" in batch:
            black_batch_idx = index
            assert_that(batch).does_not_contain("ruff")
        if "ruff" in batch:
            ruff_batch_idx = index
            assert_that(batch).does_not_contain("black")

    assert_that(black_batch_idx).is_not_none()
    assert_that(ruff_batch_idx).is_not_none()
    assert_that(black_batch_idx).is_not_equal_to(ruff_batch_idx)
