"""Unit tests for Bandit output parsing and tool JSON extraction."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.bandit.bandit_parser import parse_bandit_output
from lintro.plugins import ToolRegistry


def test_parse_bandit_valid_output() -> None:
    """Parse a representative Bandit JSON result and validate fields."""
    sample_output = {
        "results": [
            {
                "filename": "test.py",
                "line_number": 10,
                "col_offset": 4,
                "test_id": "B602",
                "test_name": ("subprocess_popen_with_shell_equals_true"),
                "issue_severity": "HIGH",
                "issue_confidence": "HIGH",
                "issue_text": (
                    "subprocess call with shell=True identified, security issue."
                ),
                "more_info": (
                    "https://bandit.readthedocs.io/en/1.8.6/plugins/"
                    "b602_subprocess_popen_with_shell_equals_true.html"
                ),
                "issue_cwe": {
                    "id": 78,
                    "link": "https://cwe.mitre.org/data/definitions/78.html",
                },
                "code": "subprocess.call(user_input, shell=True)",
                "line_range": [10],
            },
        ],
    }
    issues = parse_bandit_output(sample_output)
    assert_that(len(issues)).is_equal_to(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("test.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.col_offset).is_equal_to(4)
    assert_that(issue.test_id).is_equal_to("B602")
    assert_that(issue.issue_severity).is_equal_to("HIGH")
    assert_that(issue.issue_confidence).is_equal_to("HIGH")
    assert_that(issue.issue_text).contains("shell=True")


def test_parse_bandit_multiple_issues_and_errors_array() -> None:
    """Parser should handle multiple results and ignore errors array."""
    sample_output = {
        "errors": [{"filename": "z.py", "reason": "bad config"}],
        "results": [
            {
                "filename": "a.py",
                "line_number": 1,
                "col_offset": 0,
                "test_id": "B101",
                "test_name": "assert_used",
                "issue_severity": "LOW",
                "issue_confidence": "HIGH",
                "issue_text": "Use of assert.",
                "more_info": "https://example.com",
                "line_range": [1],
            },
            {
                "filename": "b.py",
                "line_number": 2,
                "col_offset": 1,
                "test_id": "B102",
                "test_name": "exec_used",
                "issue_severity": "MEDIUM",
                "issue_confidence": "LOW",
                "issue_text": "Use of exec.",
                "more_info": "https://example.com",
                "line_range": [2],
            },
        ],
    }
    issues = parse_bandit_output(sample_output)
    assert_that(len(issues)).is_equal_to(2)
    assert_that(issues[0].file).is_equal_to("a.py")
    assert_that(issues[1].file).is_equal_to("b.py")


def test_parse_bandit_empty_results() -> None:
    """Ensure an empty results list returns no issues."""
    issues = parse_bandit_output({"results": []})
    assert_that(issues).is_equal_to([])


def test_parse_bandit_missing_results_key() -> None:
    """Missing results should behave as empty list (no crash)."""
    issues = parse_bandit_output({})
    assert_that(issues).is_equal_to([])


def test_parse_bandit_handles_malformed_issue_gracefully(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed issue entries should be skipped with a warning.

    Args:
        caplog: Pytest logging capture fixture.
    """
    malformed = {"results": [None, 42, {"filename": "x.py", "line_number": "NaN"}]}
    issues = parse_bandit_output(malformed)
    assert_that(issues).is_equal_to([])


def test_bandit_check_parses_mixed_output_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BanditTool.check should parse JSON amidst mixed stdout/stderr text.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "a.py"
    p.write_text("print('hello')\n")
    sample = {
        "errors": [],
        "results": [
            {
                "filename": str(p),
                "line_number": 1,
                "col_offset": 0,
                "issue_severity": "LOW",
                "issue_confidence": "HIGH",
                "test_id": "B101",
                "test_name": "assert_used",
                "issue_text": "Use of assert detected.",
                "more_info": "https://example.com",
                "line_range": [1],
            },
        ],
    }
    mixed_stdout = "Working... 100%\n" + json.dumps(sample) + "\n"
    mixed_stderr = "[main] INFO done\n"

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(stdout=mixed_stdout, stderr=mixed_stderr, returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("bandit")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("bandit")
    assert_that(result.success is True).is_true()
    assert_that(result.issues_count).is_equal_to(1)


def test_bandit_check_handles_nonzero_rc_with_errors_array(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure nonzero return with JSON errors[] sets success False but parses.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "c.py"
    p.write_text("print('x')\n")
    sample = {
        "errors": [
            {"filename": str(p), "reason": "config error"},
        ],
        "results": [
            {
                "filename": str(p),
                "line_number": 1,
                "col_offset": 0,
                "issue_severity": "LOW",
                "issue_confidence": "HIGH",
                "test_id": "B101",
                "test_name": "assert_used",
                "issue_text": "Use of assert detected.",
                "more_info": "https://example.com",
                "line_range": [1],
            },
        ],
    }

    class NS:
        def __init__(self, stdout: str, stderr: str, returncode: int) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> NS:
        # Handle version check calls
        if "--version" in cmd:
            return NS(
                stdout=f"bandit {get_min_version(ToolName.BANDIT)}",
                stderr="",
                returncode=0,
            )
        # Handle actual check calls
        return NS(stdout=json.dumps(sample), stderr="", returncode=1)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("bandit")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_bandit_check_handles_unparseable_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On unparseable output, BanditTool.check should fail gracefully.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory path fixture.
    """
    p = tmp_path / "b.py"
    p.write_text("x=1\n")

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Handle version check calls
        if "--version" in cmd:
            return SimpleNamespace(
                stdout=f"bandit {get_min_version(ToolName.BANDIT)}",
                stderr="",
                returncode=0,
            )
        # Handle actual check calls
        return SimpleNamespace(stdout="nonsense", stderr="also nonsense", returncode=1)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("bandit")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("bandit")
    assert_that(result.success is False).is_true()
    assert_that(result.issues_count).is_equal_to(0)
