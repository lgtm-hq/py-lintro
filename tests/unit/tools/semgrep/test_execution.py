"""Unit tests for Semgrep plugin execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue
from lintro.tools.definitions.semgrep import SemgrepPlugin
from tests.test_samples_helpers import copy_sample

# =============================================================================
# Tests for SemgrepPlugin.check method
# =============================================================================


def test_check_with_mocked_subprocess_success(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "clean_code.py"
    test_file.write_text('"""Clean module with no security issues."""\n')

    # Semgrep JSON output with no results
    semgrep_output = json.dumps({"results": [], "errors": []})

    with patch.object(
        semgrep_plugin,
        "_run_subprocess",
        return_value=(True, semgrep_output),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_findings(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when Semgrep finds security problems.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "python",
        "semgrep",
        "semgrep_dangerous_system_call.py",
        dest_name="vulnerable.py",
    )

    # Semgrep JSON output with findings
    semgrep_output = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.audit.dangerous-system-call",
                    "path": str(test_file),
                    "start": {"line": 2, "col": 1},
                    "end": {"line": 2, "col": 25},
                    "extra": {
                        "message": "Detected dangerous system call with user input",
                        "severity": "ERROR",
                        "metadata": {
                            "category": "security",
                            "cwe": ["CWE-78"],
                        },
                    },
                },
            ],
            "errors": [],
        },
    )

    with patch.object(
        semgrep_plugin,
        "_run_subprocess",
        return_value=(False, semgrep_output),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    # Scan succeeded; findings don't cause failure
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issues = cast(list[SemgrepIssue], result.issues)
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(SemgrepIssue)
    assert_that(issue.check_id).is_equal_to(
        "python.lang.security.audit.dangerous-system-call",
    )


def test_check_with_multiple_findings(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
) -> None:
    """Check handles multiple findings correctly.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "python",
        "semgrep",
        "semgrep_multiple_findings.py",
        dest_name="multiple_issues.py",
    )

    # Semgrep JSON output with multiple findings
    semgrep_output = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.audit.dangerous-system-call",
                    "path": str(test_file),
                    "start": {"line": 2, "col": 1},
                    "end": {"line": 2, "col": 15},
                    "extra": {
                        "message": "Dangerous system call",
                        "severity": "ERROR",
                        "metadata": {"category": "security"},
                    },
                },
                {
                    "check_id": "python.lang.security.audit.eval-usage",
                    "path": str(test_file),
                    "start": {"line": 3, "col": 1},
                    "end": {"line": 3, "col": 8},
                    "extra": {
                        "message": "Use of eval() detected",
                        "severity": "WARNING",
                        "metadata": {"category": "security"},
                    },
                },
            ],
            "errors": [],
        },
    )

    with patch.object(
        semgrep_plugin,
        "_run_subprocess",
        return_value=(False, semgrep_output),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.issues).is_length(2)


# =============================================================================
# Tests for SemgrepPlugin.fix method
# =============================================================================


def test_fix_raises_not_implemented(semgrep_plugin: SemgrepPlugin) -> None:
    """Fix method raises NotImplementedError.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        semgrep_plugin.fix(["src/"], {})
