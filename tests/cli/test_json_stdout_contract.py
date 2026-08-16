"""Pin the JSON stdout contract the release binary smoke gate depends on.

``scripts/ci/smoke-test-binary.py`` calls ``json.loads`` on the full stdout of
three CLI invocations (``list-tools --json``, ``config --json``, and
``check --output-format json``). Those calls run only at release time, so a
stray banner or warning on stdout would turn a green PR into a red smoke
gate weeks later. These tests pin the same contract against the live CLI.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.plugins.discovery import discover_builtin_tools
from lintro.plugins.registry import ToolRegistry

# Tools that are registered builtins but are not installed by ``uv sync``.
# Used to force the degraded (unavailable-tool) path without mocking.
_UNAVAILABLE_TOOL_CANDIDATES: tuple[str, ...] = (
    "hadolint",
    "actionlint",
    "gitleaks",
    "taplo",
    "shellcheck",
)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner that captures stdout and stderr separately.

    Returns:
        CliRunner: A Click test runner instance.
    """
    return CliRunner()


def _ensure_builtin_origins() -> None:
    """Restore builtin origin labels after cross-test registry pollution.

    Plugin unit tests call ``ToolRegistry.clear()`` and restore ``_tools`` /
    ``_instances`` without ``_origins``. ``get_origin`` then returns
    ``"unknown"``, which would make the smoke-gate builtin filter fail even
    though the CLI still emitted a valid JSON document. Re-stamp missing
    origins so this contract test matches a clean process.

    Returns:
        None.
    """
    discover_builtin_tools()
    with ToolRegistry._lock:
        for name in ToolRegistry._tools:
            if name not in ToolRegistry._origins:
                ToolRegistry._origins[name] = ToolRegistry.BUILTIN_ORIGIN


def _parse_stdout_json(result: Any) -> Any:
    """Parse the entire captured stdout as a single JSON document.

    The smoke gate calls ``json.loads`` on the full stdout capture, not a
    line scan. Any incidental print (banner, warning, log line) must fail
    here the same way it fails the release smoke test.

    Args:
        result: Click ``Result`` from ``CliRunner.invoke``.

    Returns:
        The parsed JSON payload.

    Raises:
        json.JSONDecodeError: When stdout is not a single JSON document.
    """
    return json.loads(result.stdout)


def test_list_tools_json_stdout_is_a_single_document(
    cli_runner: CliRunner,
) -> None:
    """``list-tools --json`` stdout is one JSON object of tool metadata.

    The smoke test consumes a non-empty dict whose values carry ``origin``
    so it can count builtin tools.

    Args:
        cli_runner: Click test runner instance.
    """
    _ensure_builtin_origins()
    result = cli_runner.invoke(cli, ["list-tools", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    tools = _parse_stdout_json(result)
    assert_that(tools).is_instance_of(dict)
    assert_that(tools).is_not_empty()

    builtins = [
        name
        for name, meta in tools.items()
        if isinstance(meta, dict) and meta.get("origin") == "builtin"
    ]
    assert_that(builtins).is_not_empty()
    assert_that(tools).contains_key("ruff")
    assert_that(tools["ruff"]).contains_key("origin")


def test_config_json_stdout_is_a_single_document(
    cli_runner: CliRunner,
) -> None:
    """``config --json`` stdout is one JSON object with an execution order.

    The smoke test consumes ``tool_execution_order`` and requires at least
    one builtin name in that list.

    Args:
        cli_runner: Click test runner instance.
    """
    result = cli_runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = _parse_stdout_json(result)
    assert_that(payload).is_instance_of(dict)
    assert_that(payload).contains_key("tool_execution_order")
    order = payload["tool_execution_order"]
    assert_that(order).is_instance_of(list)
    assert_that(order).is_not_empty()
    ordered_names = [
        str(entry.get("tool", "")) if isinstance(entry, dict) else str(entry)
        for entry in order
    ]
    assert_that(ordered_names).contains("ruff")


def test_check_json_stdout_is_a_single_document(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """``check --output-format json`` stdout is one JSON object with results.

    The smoke test consumes ``results[].tool`` as evidence the registry
    dispatched a builtin. A small fixture tree plus ``ruff`` (always
    present in the test venv) is enough to pin that shape.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory for the fixture tree.
    """
    (tmp_path / "sample.py").write_text("x = 1\n")

    result = cli_runner.invoke(
        cli,
        [
            "check",
            "--output-format",
            "json",
            "--tools",
            "ruff",
            str(tmp_path),
        ],
    )

    assert_that(result.exit_code).is_in(0, 1)
    payload = _parse_stdout_json(result)
    assert_that(payload).is_instance_of(dict)
    assert_that(payload).contains_key("results")
    results = payload["results"]
    assert_that(results).is_instance_of(list)
    assert_that(results).is_not_empty()
    tool_names = [entry.get("tool") for entry in results if isinstance(entry, dict)]
    assert_that(tool_names).contains("ruff")


def test_check_json_unavailable_tool_keeps_stdout_pure(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Unavailable-tool warnings go to stderr; stdout stays one JSON document.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory for the fixture tree.
    """
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "sample.py").write_text("x = 1\n")

    tool = _first_unavailable_tool()
    result = cli_runner.invoke(
        cli,
        [
            "check",
            "--output-format",
            "json",
            "--tools",
            tool,
            str(tmp_path),
        ],
    )

    assert_that(result.exit_code).is_in(0, 1)
    payload = _parse_stdout_json(result)
    assert_that(payload).is_instance_of(dict)
    assert_that(payload).contains_key("results")
    assert_that(result.stderr).is_not_empty()
    stderr_text = result.stderr.lower()
    assert_that(
        "skip" in stderr_text or "not found" in stderr_text or "warning" in stderr_text,
    ).is_true()


def _first_unavailable_tool() -> str:
    """Return a registered builtin whose executable is not on PATH.

    Returns:
        Tool name to pass to ``--tools``.

    Raises:
        pytest.fail: When every candidate binary is unexpectedly installed.
    """
    for name in _UNAVAILABLE_TOOL_CANDIDATES:
        if shutil.which(name) is None:
            return name
    pytest.fail(
        "expected at least one optional tool to be missing from PATH; "
        f"found all of {_UNAVAILABLE_TOOL_CANDIDATES}",
    )
