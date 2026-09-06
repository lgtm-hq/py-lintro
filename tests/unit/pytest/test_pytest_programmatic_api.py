"""Unit tests for pytest programmatic API.

These drive the programmatic ``test()`` wrapper against a real throwaway
project rather than patching the pipeline, so each assertion reads a value a
caller can actually see: the report file lintro writes, its format, and the
exit code the wrapper raises (#2315). The pure option pass-throughs that used
to be asserted through mock call bookkeeping are covered at the CLI level in
``test_pytest_cli_options.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.cli_utils.commands.test import test


def _write_project(*, tmp_path: Path, body: str) -> Path:
    """Create a one-file throwaway pytest project.

    Args:
        tmp_path: Pytest temporary directory for the test.
        body: Source of the generated ``test_generated.py`` module.

    Returns:
        Path: Directory holding the generated test module.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_generated.py").write_text(body, encoding="utf-8")
    return project


def _pytest_summary(*, report_text: str) -> dict[str, int]:
    """Extract pytest's outcome counts from a rendered Lintro report.

    Comparing parsed integers rather than searching for ``'"passed": 1'``
    matters: that substring also matches ``"passed": 10`` (#2375).

    Args:
        report_text: Full text of the report file the run wrote.

    Returns:
        The outcome counts keyed by name, defaulting each to ``0``.

    Raises:
        AssertionError: If the report carries no recognisable counts.
    """
    counts = {
        name: int(value)
        for name, value in re.findall(
            r'"(passed|failed|skipped|error)":\s*(\d+)',
            report_text,
        )
    }
    if not counts:
        raise AssertionError(f"no pytest counts in report: {report_text[:400]}")
    return {key: counts.get(key, 0) for key in ("passed", "failed", "skipped", "error")}


@pytest.mark.slow
def test_test_function_runs_the_suite_and_writes_the_report_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing suite returns cleanly and its outcome reaches the report file.

    Args:
        tmp_path: Pytest temporary directory for the generated project.
        monkeypatch: Pytest monkeypatch fixture, used to run from the project.
    """
    project = _write_project(
        tmp_path=tmp_path,
        body="def test_generated_passes() -> None:\n    assert True\n",
    )
    report = tmp_path / "report.txt"
    # The plugin auto-enables --junitxml at a path relative to the cwd, so two
    # nested runs would race on one report.xml in the repository root under
    # ``-n auto``. Run from the throwaway project instead (#2375).
    monkeypatch.chdir(project)

    test(
        paths=(str(project),),
        exclude=None,
        include_venv=False,
        output=str(report),
        output_format="grid",
        group_by="file",
        verbose=False,
        tool_options=None,
        yes=True,
    )

    written = report.read_text(encoding="utf-8")
    assert_that(written).contains("Lintro Test Report")
    summary = _pytest_summary(report_text=written)
    assert_that(summary["passed"]).is_equal_to(1)
    assert_that(summary["failed"]).is_equal_to(0)


def test_test_function_normalizes_bare_tool_options_to_the_pytest_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``collect_only`` fragment reaches pytest as ``pytest:collect_only``.

    The wrapper namespaces every ``--tool-options`` fragment for the pytest
    tool. When that works the run only collects, so the report lists the test
    names and records no test outcome.

    Args:
        tmp_path: Pytest temporary directory for the generated project.
        monkeypatch: Pytest monkeypatch fixture, used to run from the project.
    """
    project = _write_project(
        tmp_path=tmp_path,
        body=(
            "def test_generated_first() -> None:\n"
            "    assert True\n"
            "\n"
            "\n"
            "def test_generated_second() -> None:\n"
            "    assert True\n"
        ),
    )
    report = tmp_path / "report.txt"
    # The plugin auto-enables --junitxml at a path relative to the cwd, so two
    # nested runs would race on one report.xml in the repository root under
    # ``-n auto``. Run from the throwaway project instead (#2375).
    monkeypatch.chdir(project)

    test(
        paths=(str(project),),
        exclude=None,
        include_venv=False,
        output=str(report),
        output_format="grid",
        group_by="file",
        verbose=False,
        tool_options="collect_only=True",
        yes=True,
    )

    written = report.read_text(encoding="utf-8")
    assert_that(written).contains("Collected 2 test(s):")
    assert_that(written).contains("test_generated_first")
    assert_that(written).contains("test_generated_second")
    assert_that(written).does_not_contain('"passed"')


def test_test_function_writes_json_when_asked_for_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``output_format="json"`` produces a parseable JSON report.

    Args:
        tmp_path: Pytest temporary directory for the generated project.
        monkeypatch: Pytest monkeypatch fixture, used to run from the project.
    """
    project = _write_project(
        tmp_path=tmp_path,
        body="def test_generated_passes() -> None:\n    assert True\n",
    )
    report = tmp_path / "report.json"
    # The plugin auto-enables --junitxml at a path relative to the cwd, so two
    # nested runs would race on one report.xml in the repository root under
    # ``-n auto``. Run from the throwaway project instead (#2375).
    monkeypatch.chdir(project)

    test(
        paths=(str(project),),
        exclude=None,
        include_venv=False,
        output=str(report),
        output_format="json",
        group_by="file",
        verbose=False,
        tool_options="collect_only=True",
        yes=True,
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert_that(payload["action"]).is_equal_to("test")
    assert_that([entry["tool"] for entry in payload["results"]]).contains("pytest")


@pytest.mark.slow
def test_test_function_exits_with_the_failing_suite_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing suite exits non-zero and the failure lands in the report.

    Args:
        tmp_path: Pytest temporary directory for the generated project.
        monkeypatch: Pytest monkeypatch fixture, used to run from the project.
    """
    project = _write_project(
        tmp_path=tmp_path,
        body="def test_generated_fails() -> None:\n    assert False\n",
    )
    report = tmp_path / "report.txt"
    # The plugin auto-enables --junitxml at a path relative to the cwd, so two
    # nested runs would race on one report.xml in the repository root under
    # ``-n auto``. Run from the throwaway project instead (#2375).
    monkeypatch.chdir(project)

    with pytest.raises(SystemExit) as exc_info:
        test(
            paths=(str(project),),
            exclude=None,
            include_venv=False,
            output=str(report),
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
            yes=True,
        )

    assert_that(exc_info.value.code).is_equal_to(1)
    written = report.read_text(encoding="utf-8")
    summary = _pytest_summary(report_text=written)
    assert_that(summary["failed"]).is_equal_to(1)
    assert_that(written).contains("test_generated_fails")
