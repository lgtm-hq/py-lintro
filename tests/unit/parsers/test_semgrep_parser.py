"""Unit tests for Semgrep output parsing and tool JSON extraction."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.semgrep.semgrep_parser import parse_semgrep_output
from lintro.plugins import ToolRegistry
from lintro.tools.core.version_parsing import get_minimum_versions


def _semgrep_version_stdout() -> str:
    """Return a ``semgrep --version`` line that satisfies the current pin.

    Returns:
        Version stdout matching the generator-owned semgrep pin.
    """
    return f"semgrep {get_minimum_versions()['semgrep']}"


def test_parse_semgrep_valid_output() -> None:
    """Parse a representative Semgrep JSON result and validate fields."""
    sample_output = {
        "results": [
            {
                "check_id": "python.lang.security.audit.dangerous-subprocess-use",
                "path": "app.py",
                "start": {"line": 15, "col": 5},
                "end": {"line": 15, "col": 45},
                "extra": {
                    "message": "Detected subprocess call with shell=True",
                    "severity": "WARNING",
                    "metadata": {
                        "category": "security",
                        "cwe": ["CWE-78"],
                    },
                },
            },
        ],
        "errors": [],
    }
    output = json.dumps(sample_output)
    issues = parse_semgrep_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("app.py")
    assert_that(issue.line).is_equal_to(15)
    assert_that(issue.column).is_equal_to(5)
    assert_that(issue.end_line).is_equal_to(15)
    assert_that(issue.end_column).is_equal_to(45)
    assert_that(issue.check_id).is_equal_to(
        "python.lang.security.audit.dangerous-subprocess-use",
    )
    assert_that(issue.severity).is_equal_to("WARNING")
    assert_that(issue.category).is_equal_to("security")
    assert_that(issue.cwe).is_equal_to(["CWE-78"])
    assert_that(issue.message).contains("shell=True")


def test_parse_semgrep_multiple_issues() -> None:
    """Parser should handle multiple results correctly."""
    sample_output = {
        "results": [
            {
                "check_id": "python.lang.security.audit.sql-injection",
                "path": "a.py",
                "start": {"line": 10, "col": 1},
                "end": {"line": 10, "col": 50},
                "extra": {
                    "message": "SQL injection detected",
                    "severity": "ERROR",
                    "metadata": {"category": "security"},
                },
            },
            {
                "check_id": "python.lang.security.audit.hardcoded-password",
                "path": "b.py",
                "start": {"line": 5, "col": 1},
                "end": {"line": 5, "col": 30},
                "extra": {
                    "message": "Hardcoded password detected",
                    "severity": "WARNING",
                    "metadata": {"category": "security"},
                },
            },
        ],
        "errors": [],
    }
    output = json.dumps(sample_output)
    issues = parse_semgrep_output(output=output)
    assert_that(len(issues)).is_equal_to(2)
    assert_that(issues[0].file).is_equal_to("a.py")
    assert_that(issues[0].severity).is_equal_to("ERROR")
    assert_that(issues[1].file).is_equal_to("b.py")
    assert_that(issues[1].severity).is_equal_to("WARNING")


def test_parse_semgrep_empty_results() -> None:
    """Ensure an empty results list returns no issues."""
    issues = parse_semgrep_output(output=json.dumps({"results": []}))
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_none_output() -> None:
    """None output should return empty list."""
    issues = parse_semgrep_output(output=None)
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_empty_string_output() -> None:
    """Empty string output should return empty list."""
    issues = parse_semgrep_output(output="")
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_missing_results_key() -> None:
    """Missing results should behave as empty list (no crash)."""
    issues = parse_semgrep_output(output=json.dumps({}))
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_handles_malformed_issue_gracefully() -> None:
    """Malformed issue entries should be skipped gracefully.

    Note: Warnings are logged via loguru (visible in test output when run with
    -s flag) but not asserted here due to loguru's capture complexity.
    """
    malformed = {
        "results": [
            None,
            42,
            {"check_id": "test", "path": "x.py", "start": {"line": "NaN"}},
        ],
    }
    issues = parse_semgrep_output(output=json.dumps(malformed))
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_cwe_as_string() -> None:
    """CWE can be a single string instead of list."""
    sample_output = {
        "results": [
            {
                "check_id": "test.rule",
                "path": "test.py",
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 10},
                "extra": {
                    "message": "Test issue",
                    "severity": "INFO",
                    "metadata": {
                        "category": "correctness",
                        "cwe": "CWE-123",
                    },
                },
            },
        ],
    }
    output = json.dumps(sample_output)
    issues = parse_semgrep_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].cwe).is_equal_to(["CWE-123"])


def test_parse_semgrep_missing_optional_fields() -> None:
    """Parser should handle missing optional fields gracefully."""
    sample_output = {
        "results": [
            {
                "check_id": "test.rule",
                "path": "test.py",
                "start": {"line": 1},
                "end": {"line": 1},
                "extra": {
                    "message": "Test issue",
                },
            },
        ],
    }
    output = json.dumps(sample_output)
    issues = parse_semgrep_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    issue = issues[0]
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.end_column).is_equal_to(0)
    assert_that(issue.severity).is_equal_to("WARNING")
    assert_that(issue.category).is_equal_to("")
    assert_that(issue.cwe).is_none()


def test_parse_semgrep_invalid_json() -> None:
    """Invalid JSON should return empty list without crashing."""
    issues = parse_semgrep_output(output="not valid json")
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_non_object_json() -> None:
    """Non-object JSON should return empty list and log warning."""
    issues = parse_semgrep_output(output=json.dumps([1, 2, 3]))
    assert_that(issues).is_equal_to([])


def test_parse_semgrep_non_list_results() -> None:
    """Non-list results should return empty list and log warning."""
    issues = parse_semgrep_output(output=json.dumps({"results": "not a list"}))
    assert_that(issues).is_equal_to([])


def test_semgrep_check_parses_mixed_output_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SemgrepTool.check should parse JSON amidst mixed stdout/stderr text.

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
                "check_id": "test.rule",
                "path": str(p),
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 15},
                "extra": {
                    "message": "Test issue detected.",
                    "severity": "INFO",
                    "metadata": {"category": "correctness"},
                },
            },
        ],
    }
    mixed_stdout = "Running semgrep...\n" + json.dumps(sample) + "\n"
    mixed_stderr = "[main] INFO done\n"

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
                stdout=_semgrep_version_stdout(),
                stderr="",
                returncode=0,
            )
        # Handle actual check calls
        return SimpleNamespace(
            stdout=mixed_stdout,
            stderr=mixed_stderr,
            returncode=0,
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("semgrep")
    assert_that(result.success is True).is_true()
    assert_that(result.issues_count).is_equal_to(1)


def test_semgrep_check_handles_nonzero_rc_with_errors_array(
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
            {"path": str(p), "message": "config error"},
        ],
        "results": [
            {
                "check_id": "test.rule",
                "path": str(p),
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 10},
                "extra": {
                    "message": "Test issue detected.",
                    "severity": "WARNING",
                    "metadata": {"category": "security"},
                },
            },
        ],
    }

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
                stdout=_semgrep_version_stdout(),
                stderr="",
                returncode=0,
            )
        # Handle actual check calls
        return SimpleNamespace(stdout=json.dumps(sample), stderr="", returncode=1)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_semgrep_check_handles_unparseable_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On unparseable output, SemgrepTool.check should fail gracefully.

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
                stdout=_semgrep_version_stdout(),
                stderr="",
                returncode=0,
            )
        # Handle actual check calls
        return SimpleNamespace(stdout="nonsense", stderr="also nonsense", returncode=1)

    monkeypatch.setattr("subprocess.run", fake_run)
    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()
    result: ToolResult = tool.check([str(p)], {})
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("semgrep")
    assert_that(result.success is False).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_semgrep_issue_display_row() -> None:
    """Test that SemgrepIssue.to_display_row returns correct values."""
    from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue

    issue = SemgrepIssue(
        file="test.py",
        line=10,
        column=5,
        message="Test message",
        check_id="test.rule.id",
        severity="ERROR",
    )
    row = issue.to_display_row()
    assert_that(row["file"]).is_equal_to("test.py")
    assert_that(row["line"]).is_equal_to("10")
    assert_that(row["column"]).is_equal_to("5")
    assert_that(row["code"]).is_equal_to("test.rule.id")
    assert_that(row["severity"]).is_equal_to("ERROR")


def test_semgrep_tool_definition() -> None:
    """Test that Semgrep tool definition has correct values."""
    from lintro.enums.tool_type import ToolType

    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()
    defn = tool.definition
    assert_that(defn.name).is_equal_to("semgrep")
    assert_that(defn.can_fix).is_false()
    assert_that(defn.tool_type).is_equal_to(ToolType.LINTER | ToolType.SECURITY)
    assert_that(defn.min_version).is_equal_to(get_minimum_versions()["semgrep"])
    assert_that("*.py" in defn.file_patterns).is_true()
    assert_that("*.js" in defn.file_patterns).is_true()
    assert_that("*.go" in defn.file_patterns).is_true()


def test_semgrep_set_options_validates_severity() -> None:
    """Test that set_options validates severity correctly."""
    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()

    # Valid severity should work
    tool.set_options(severity="WARNING")
    assert_that(tool.options.get("severity")).is_equal_to("WARNING")

    # Invalid severity should raise
    with pytest.raises(ValueError, match="Invalid Semgrep severity"):
        tool.set_options(severity="INVALID")


def test_semgrep_set_options_validates_jobs() -> None:
    """Test that set_options validates jobs correctly."""
    tool = ToolRegistry.get("semgrep")
    assert_that(tool).is_not_none()

    # Valid jobs should work
    tool.set_options(jobs=4)
    assert_that(tool.options.get("jobs")).is_equal_to(4)

    # Invalid jobs should raise
    with pytest.raises(ValueError, match="jobs must be a positive integer"):
        tool.set_options(jobs=0)

    with pytest.raises(ValueError, match="jobs must be a positive integer"):
        tool.set_options(jobs=-1)
