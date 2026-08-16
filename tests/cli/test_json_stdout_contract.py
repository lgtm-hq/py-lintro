"""Pin the JSON stdout contract the release binary smoke gate depends on.

``scripts/ci/smoke-test-binary.py`` calls ``json.loads`` on the full stdout of
three CLI invocations (``list-tools --json``, ``config --json``, and
``check --output-format json``). Those calls run only at release time, so a
stray banner or warning on stdout would turn a green PR into a red smoke
gate weeks later. These tests pin the same contract against the live CLI.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.plugins._builtin_index import REGISTERING_TOOL_MODULES
from lintro.plugins.discovery import discover_builtin_tools
from lintro.plugins.registry import ToolRegistry

# Always-registered optional builtin used to force the degraded path. The
# executable is hidden via an isolated PATH so the test does not depend on
# whether ``install-tools.sh --local`` put the binary on the host.
_UNAVAILABLE_TOOL = "hadolint"


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner that captures stdout and stderr separately.

    Returns:
        CliRunner: A Click test runner instance.
    """
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_registry() -> Generator[None]:
    """Snapshot and restore the full registry, including origins.

    Yields:
        None: Registry snapshot for the test duration.
    """
    with ToolRegistry._lock:
        original_tools = dict(ToolRegistry._tools)
        original_instances = dict(ToolRegistry._instances)
        original_origins = dict(ToolRegistry._origins)
    try:
        yield
    finally:
        with ToolRegistry._lock:
            ToolRegistry._tools = original_tools
            ToolRegistry._instances = original_instances
            ToolRegistry._origins = original_origins


def _require_discovered_builtin_origins() -> None:
    """Discover builtins and fail if any registered tool lacks an origin.

    Plugin fixtures that restore ``_tools`` / ``_instances`` without
    ``_origins`` used to make ``get_origin`` return ``unknown``. Restamping
    those as ``builtin`` would hide the same class of bug the smoke gate
    counts. Fail instead so polluting fixtures stay visible.

    Returns:
        None.
    """
    discover_builtin_tools()
    with ToolRegistry._lock:
        missing = sorted(
            name for name in ToolRegistry._tools if name not in ToolRegistry._origins
        )
    if missing:
        pytest.fail(
            "tool origins missing after discover_builtin_tools(); "
            f"registry pollution left {missing} without origin",
        )


def _parse_stdout_json(result: Any) -> Any:
    """Parse the entire captured stdout as a single JSON document.

    The smoke gate calls ``json.loads`` on the full stdout capture, not a
    line scan. Any incidental print (banner, warning, log line) must fail
    here the same way it fails the release smoke test.

    Args:
        result: Click ``Result`` from ``CliRunner.invoke``.

    Returns:
        The parsed JSON payload.
    """
    return json.loads(result.stdout)


def _assert_tool_metadata_entries(tools: Any) -> None:
    """Assert every list-tools value is a mapping with ``origin``.

    Args:
        tools: Parsed ``list-tools --json`` payload.

    Returns:
        None.
    """
    assert_that(tools).is_instance_of(dict)
    assert_that(tools).is_not_empty()
    for name, meta in tools.items():
        assert_that(meta).described_as(f"metadata for {name}").is_instance_of(dict)
        assert_that(meta).described_as(f"metadata for {name}").contains_key("origin")


def _assert_result_entries(results: Any) -> None:
    """Assert every check result is a mapping with ``tool``.

    Args:
        results: Parsed ``results`` array from ``check --output-format json``.

    Returns:
        None.
    """
    assert_that(results).is_instance_of(list)
    assert_that(results).is_not_empty()
    for entry in results:
        assert_that(entry).is_instance_of(dict)
        assert_that(entry).contains_key("tool")


def test_list_tools_json_stdout_is_a_single_document(
    cli_runner: CliRunner,
) -> None:
    """``list-tools --json`` stdout is one JSON object of every builtin.

    The smoke test requires every ``REGISTERING_TOOL_MODULES`` name to
    appear as ``origin==builtin``, not merely a non-empty builtin set.

    Args:
        cli_runner: Click test runner instance.
    """
    _require_discovered_builtin_origins()
    result = cli_runner.invoke(cli, ["list-tools", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    tools = _parse_stdout_json(result)
    _assert_tool_metadata_entries(tools)
    assert_that(tools).contains_key("ruff")
    assert_that(tools["ruff"]).contains_key("origin")

    builtins = [name for name, meta in tools.items() if meta.get("origin") == "builtin"]
    reported = {name.replace("-", "_") for name in builtins}
    missing = [
        name
        for name in REGISTERING_TOOL_MODULES
        if name.replace("-", "_") not in reported
    ]
    assert_that(missing).is_empty()


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

    The smoke gate runs the default all-tools argv (300s timeout) on
    ``sample.py`` + ``sample.yaml``. This test keeps ``--tools ruff`` because
    ``pytest.ini`` caps each test at 120s; list-tools above pins builtin
    completeness instead. A small fixture tree plus ``ruff`` (always present
    in the test venv) is enough to pin the stdout shape.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory for the fixture tree.
    """
    (tmp_path / "sample.py").write_text("x = 1\n")
    (tmp_path / "sample.yaml").write_text("key: value\n")

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
    _assert_result_entries(results)
    tool_names = [entry["tool"] for entry in results]
    assert_that(tool_names).contains("ruff")


def test_check_json_unavailable_tool_keeps_stdout_pure(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable-tool warnings go to stderr; stdout stays one JSON document.

    PATH is isolated so ``hadolint`` is missing regardless of host installs.
    The result object must name that tool the same way the smoke gate counts
    ``results[].tool``.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory for the fixture tree.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "sample.py").write_text("x = 1\n")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    result = cli_runner.invoke(
        cli,
        [
            "check",
            "--output-format",
            "json",
            "--tools",
            _UNAVAILABLE_TOOL,
            str(tmp_path),
        ],
    )

    assert_that(result.exit_code).is_in(0, 1)
    payload = _parse_stdout_json(result)
    assert_that(payload).is_instance_of(dict)
    assert_that(payload).contains_key("results")
    results = payload["results"]
    _assert_result_entries(results)
    tool_names = [entry["tool"] for entry in results]
    assert_that(tool_names).contains(_UNAVAILABLE_TOOL)
    skipped = next(entry for entry in results if entry["tool"] == _UNAVAILABLE_TOOL)
    assert_that(skipped.get("skipped")).is_true()
    assert_that(skipped.get("skip_reason")).is_not_none()
    assert_that(result.stderr.lower()).contains(_UNAVAILABLE_TOOL)
