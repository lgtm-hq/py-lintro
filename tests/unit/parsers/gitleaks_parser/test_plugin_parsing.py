"""Unit tests for GitleaksPlugin parsing integration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry


def _get_report_path(cmd: list[str]) -> str | None:
    """Extract the report path from a gitleaks command.

    Args:
        cmd: The command list.

    Returns:
        The report path if found, None otherwise.
    """
    try:
        idx = cmd.index("--report-path")
        return cmd[idx + 1]
    except (ValueError, IndexError):
        return None


def test_gitleaks_check_parses_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """GitleaksPlugin.check should parse JSON output correctly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "secret.py"
    p.write_text("API_KEY = 'sk_test_example'\n")

    sample = [
        {
            "Description": "Generic API Key",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 11,
            "EndColumn": 27,
            "File": str(p),
            "RuleID": "generic-api-key",
            "Fingerprint": f"{p}:generic-api-key:1",
        },
    ]

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Handle version check calls (check for --version flag)
        if "--version" in cmd or "version" in cmd:
            return SimpleNamespace(stdout="8.30.1", stderr="", returncode=0)
        # Handle actual check calls - write JSON to the report file
        report_path = _get_report_path(cmd)
        if report_path:
            Path(report_path).write_text(json.dumps(sample))
        return SimpleNamespace(
            stdout="",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("gitleaks")
    assert_that(tool).is_not_none()

    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result).is_instance_of(ToolResult)
    assert_that(result.name).is_equal_to("gitleaks")
    assert_that(result.success).is_true()  # success=True means tool ran, not no issues
    assert_that(result.issues_count).is_equal_to(1)


def test_gitleaks_check_handles_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """GitleaksPlugin.check should handle clean output.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "clean.py"
    p.write_text("x = 1\n")

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Handle version check calls (check for --version flag)
        if "--version" in cmd or "version" in cmd:
            return SimpleNamespace(stdout="8.30.1", stderr="", returncode=0)
        # Handle actual check calls - write empty array to report file
        report_path = _get_report_path(cmd)
        if report_path:
            Path(report_path).write_text("[]")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("gitleaks")
    assert_that(tool).is_not_none()

    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_gitleaks_check_handles_unparseable_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On unparseable output, GitleaksPlugin.check should fail gracefully.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "test.py"
    p.write_text("x = 1\n")

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Handle version check calls (check for --version flag)
        if "--version" in cmd or "version" in cmd:
            return SimpleNamespace(stdout="8.30.1", stderr="", returncode=0)
        # Handle actual check calls - write invalid JSON to report file
        report_path = _get_report_path(cmd)
        if report_path:
            Path(report_path).write_text("not json")
        return SimpleNamespace(stdout="", stderr="error", returncode=1)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("gitleaks")
    assert_that(tool).is_not_none()

    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result).is_instance_of(ToolResult)
    assert_that(result.name).is_equal_to("gitleaks")
    # Parser raises ValueError for invalid JSON, which is caught and reported as failure
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
