"""Unit tests for Semgrep plugin error handling."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.semgrep.semgrep_parser import parse_semgrep_output
from lintro.tools.definitions.semgrep import SemgrepPlugin

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for timeout handling
# =============================================================================


def test_check_with_timeout(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "large_file.py"
    test_file.write_text('"""Large file that takes too long."""\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            semgrep_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["semgrep"], timeout=120),
        ),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_with_json_parse_error(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check handles JSON parse errors gracefully.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    # Invalid JSON output
    invalid_output = "Error: Something went wrong\n{invalid json"

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            semgrep_plugin,
            "_run_subprocess",
            return_value=(False, invalid_output),
        ),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_semgrep_errors(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check handles Semgrep errors in response.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    # Semgrep JSON output with errors
    semgrep_output = json.dumps(
        {
            "results": [],
            "errors": [
                {"message": "Failed to fetch rules from registry"},
            ],
        },
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            semgrep_plugin,
            "_run_subprocess",
            return_value=(False, semgrep_output),
        ),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()


# =============================================================================
# Tests for output parsing
# =============================================================================


def test_parse_semgrep_output_single_issue() -> None:
    """Parse single issue from Semgrep output."""
    output = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.audit.eval-usage",
                    "path": "test.py",
                    "start": {"line": 10, "col": 1},
                    "end": {"line": 10, "col": 15},
                    "extra": {
                        "message": "Detected use of eval()",
                        "severity": "WARNING",
                        "metadata": {"category": "security"},
                    },
                },
            ],
        },
    )
    issues = parse_semgrep_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("test.py")
    assert_that(issues[0].line).is_equal_to(10)
    assert_that(issues[0].check_id).is_equal_to("python.lang.security.audit.eval-usage")
    assert_that(issues[0].message).contains("eval()")


def test_parse_semgrep_output_multiple_issues() -> None:
    """Parse multiple issues from Semgrep output."""
    output = json.dumps(
        {
            "results": [
                {
                    "check_id": "rule1",
                    "path": "file1.py",
                    "start": {"line": 5, "col": 1},
                    "end": {"line": 5, "col": 10},
                    "extra": {
                        "message": "Issue 1",
                        "severity": "ERROR",
                        "metadata": {},
                    },
                },
                {
                    "check_id": "rule2",
                    "path": "file2.py",
                    "start": {"line": 15, "col": 1},
                    "end": {"line": 15, "col": 20},
                    "extra": {
                        "message": "Issue 2",
                        "severity": "WARNING",
                        "metadata": {},
                    },
                },
            ],
        },
    )
    issues = parse_semgrep_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].check_id).is_equal_to("rule1")
    assert_that(issues[1].check_id).is_equal_to("rule2")


def test_parse_semgrep_output_empty() -> None:
    """Parse empty output returns empty list."""
    issues = parse_semgrep_output("")

    assert_that(issues).is_empty()


def test_parse_semgrep_output_empty_results() -> None:
    """Parse output with no results returns empty list."""
    output = json.dumps({"results": []})
    issues = parse_semgrep_output(output)

    assert_that(issues).is_empty()


def test_parse_semgrep_output_with_cwe() -> None:
    """Parse output with CWE information."""
    output = json.dumps(
        {
            "results": [
                {
                    "check_id": "security-rule",
                    "path": "app.py",
                    "start": {"line": 20, "col": 1},
                    "end": {"line": 20, "col": 30},
                    "extra": {
                        "message": "SQL injection vulnerability",
                        "severity": "ERROR",
                        "metadata": {
                            "category": "security",
                            "cwe": ["CWE-89"],
                        },
                    },
                },
            ],
        },
    )
    issues = parse_semgrep_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].cwe).contains("CWE-89")


def test_parse_semgrep_output_none_input() -> None:
    """Parse None input returns empty list."""
    issues = parse_semgrep_output(None)

    assert_that(issues).is_empty()
