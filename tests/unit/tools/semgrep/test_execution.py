"""Unit tests for Semgrep plugin execution."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.semgrep.definition import SemgrepPlugin
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

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(["semgrep"], 0, semgrep_output, ""),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_none()
    assert_that(result.parse_failures_count or 0).is_zero()


@pytest.mark.parametrize("returncode", [0, 1, 2, 7, -15])
def test_check_with_mocked_subprocess_findings(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
) -> None:
    """Findings survive execution errors, while exit codes 0 and 1 succeed.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
        returncode: Semgrep findings, execution error, or signal exit status.
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

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["semgrep"],
            returncode,
            semgrep_output,
            "semgrep diagnostic",
        ),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_equal_to(returncode in (0, 1))
    if result.success:
        assert_that(result.output).is_none()
    else:
        assert_that(result.output).contains(semgrep_output, "semgrep diagnostic")
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
        "_run_subprocess_result",
        return_value=SubprocessResult(1, semgrep_output, "", semgrep_output),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.issues).is_length(2)


@pytest.mark.parametrize("returncode", [0, 1, 2])
@pytest.mark.parametrize(
    "payload",
    [
        {"errors": []},
        {"results": None, "errors": []},
        {"results": {}, "errors": []},
        {"results": "invalid", "errors": []},
    ],
    ids=["missing", "null", "object", "string"],
)
def test_check_rejects_invalid_results(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    payload: dict[str, object],
    returncode: int,
) -> None:
    """Invalid results fail parsing regardless of the subprocess exit status.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for a discoverable Python file.
        payload: Report with a missing or non-list results field.
        returncode: Successful, findings, or execution error exit code.
    """
    source = tmp_path / "scan.py"
    source.write_text("value = 1\n", encoding="utf-8")
    report = json.dumps(payload)
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(["semgrep"], returncode, report, ""),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(report)


@pytest.mark.parametrize("returncode", [2, 3, 7, -15])
def test_check_failed_process_with_empty_results(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
) -> None:
    """A valid empty array cannot hide a failed Semgrep process.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for scan input.
        returncode: Error or signal exit status outside the findings codes.
    """
    source = tmp_path / "empty_scan.py"
    source.write_text("pass\n", encoding="utf-8")
    report = json.dumps({"results": [], "errors": []})
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["semgrep"],
            returncode,
            report,
            "scan failed",
        ),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.output).contains(report, "scan failed")


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
