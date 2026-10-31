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

_VALID_FINDING = {
    "check_id": "test.rule",
    "path": "scan.py",
    "start": {"line": 2, "col": 3},
    "extra": {"message": "Test finding", "severity": "WARNING"},
}

# =============================================================================
# Tests for SemgrepPlugin.check method
# =============================================================================


@pytest.mark.parametrize("results", [[], [_VALID_FINDING]], ids=["empty", "finding"])
@pytest.mark.parametrize(
    "errors",
    [[], [{"level": "warn", "type": "PartialParsing"}]],
    ids=["no-errors", "partial-parsing-warning"],
)
@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("", ""),
        ("Running semgrep...\n", "\n[main] INFO done"),
        ("[main] INFO start\n", "\n[main] INFO done"),
        ("[1, 2] status\n", "\n[done] done"),
        ("[[main] INFO] start\n", "\n[done] done"),
        ('["[", "\\""] status\n', "\n[done] done"),
    ],
    ids=["bare", "prose", "log-tags", "array-in-prose", "nested-tags", "quoted-tags"],
)
def test_check_with_mocked_subprocess_success(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    results: list[object],
    errors: list[object],
    prefix: str,
    suffix: str,
) -> None:
    """Valid reports remain successful with or without surrounding prose.

    Args:
        semgrep_plugin: The SemgrepPlugin instance to test.
        tmp_path: Temporary directory path for test files.
        results: Empty scan or a valid finding.
        errors: No errors or a supported nonfatal warning.
        prefix: Informational text before the report.
        suffix: Informational text after the report.
    """
    test_file = tmp_path / "clean_code.py"
    test_file.write_text('"""Clean module with no security issues."""\n')

    semgrep_output = (
        prefix + json.dumps({"results": results, "errors": errors}) + suffix
    )

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(["semgrep"], 0, semgrep_output, ""),
    ):
        result = semgrep_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(len(results))
    assert_that(result.issues).is_length(len(results))
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
    assert_that(result.parse_failures_count or 0).is_zero()
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


@pytest.mark.parametrize("returncode", [1, 2, 3, 7, -15])
@pytest.mark.parametrize(
    "results",
    [
        [],
        [None, 42, {"check_id": "test", "path": "scan.py", "start": {"line": "NaN"}}],
    ],
    ids=["empty", "unparseable"],
)
def test_check_failed_process_without_parsed_findings(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
    results: list[object],
) -> None:
    """A nonzero exit without parsed findings must preserve diagnostics and fail.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for scan input.
        returncode: Findings, error, or signal exit status.
        results: Empty results or entries the parser cannot turn into findings.
    """
    source = tmp_path / "empty_scan.py"
    source.write_text("pass\n", encoding="utf-8")
    report = json.dumps({"results": results, "errors": []})
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["semgrep"],
            returncode,
            report + "\n",
            "scan failed",
        ),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count or 0).is_equal_to(len(results))
    assert_that(result.output).is_equal_to(f"{report}\nscan failed")


@pytest.mark.parametrize("returncode", [0, 1, 2])
@pytest.mark.parametrize(
    ("results", "expected_count", "dropped"),
    [
        ([None, 42], 0, 2),
        (
            [{}, {"check_id": "test", "path": "scan.py", "start": {"line": "NaN"}}],
            0,
            2,
        ),
        ([_VALID_FINDING, None, 42, {}], 1, 3),
    ],
    ids=["nonobjects", "invalid-fields", "mixed"],
)
def test_check_counts_dropped_findings(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
    results: list[object],
    expected_count: int,
    dropped: int,
) -> None:
    """Dropped entries fail the scan while preserving usable findings and output.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for scan input.
        returncode: Successful, findings, or execution error exit status.
        results: Report entries including malformed findings.
        expected_count: Number of usable findings.
        dropped: Number of malformed findings.
    """
    source = tmp_path / "scan.py"
    source.write_text("pass\n", encoding="utf-8")
    report = json.dumps({"results": results, "errors": []})
    with patch.object(
        semgrep_plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(
            returncode,
            report,
            "scan diagnostic",
            report + "\nscan diagnostic",
        ),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(expected_count)
    assert_that(result.issues).is_length(expected_count)
    assert_that(result.parse_failures_count).is_equal_to(dropped)
    assert_that(result.output).is_equal_to(f"{report}\nscan diagnostic")
    if expected_count:
        issue = cast(list[SemgrepIssue], result.issues)[0]
        assert_that((issue.check_id, issue.file, issue.line)).is_equal_to(
            ("test.rule", "scan.py", 2),
        )


@pytest.mark.parametrize("returncode", [0, 1, 2])
@pytest.mark.parametrize(
    ("results", "expected_count", "finding_drops"),
    [([], 0, 0), ([_VALID_FINDING, None], 1, 1)],
    ids=["empty-results", "mixed-results"],
)
@pytest.mark.parametrize(
    ("errors", "dropped"),
    [
        (None, 1),
        ({}, 1),
        ("invalid", 1),
        (42, 1),
        ([None, 42], 2),
        ([None, {"level": "warn", "type": "PartialParsing"}], 1),
        ([42, {"message": "fatal error"}], 1),
    ],
    ids=[
        "null",
        "object",
        "string",
        "number",
        "nonobjects",
        "mixed-warning",
        "mixed-fatal",
    ],
)
def test_check_counts_malformed_errors(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
    errors: object,
    dropped: int,
    results: list[object],
    expected_count: int,
    finding_drops: int,
) -> None:
    """Malformed error records fail gracefully without losing parsed findings.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for scan input.
        returncode: Successful, findings, or execution error exit status.
        errors: Malformed errors field or entries.
        dropped: Number of malformed error containers or entries.
        results: Empty scan or mixed valid and malformed findings.
        expected_count: Number of findings to retain.
        finding_drops: Number of malformed findings to count alongside errors.
    """
    source = tmp_path / "scan.py"
    source.write_text("pass\n", encoding="utf-8")
    report = json.dumps({"results": results, "errors": errors})
    with patch.object(
        semgrep_plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(returncode, report, "", report),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(expected_count)
    assert_that(result.issues).is_length(expected_count)
    if expected_count:
        issue = cast(list[SemgrepIssue], result.issues)[0]
        assert_that(issue.message).is_equal_to("[test.rule] WARNING: Test finding")
    assert_that(result.parse_failures_count).is_equal_to(dropped + finding_drops)
    assert_that(result.output).is_equal_to(report)


@pytest.mark.parametrize("returncode", [0, 1, 2])
@pytest.mark.parametrize(
    "entries",
    [
        [{"results": [], "errors": []}],
        [None, {"results": [], "errors": []}, 42],
        [[{"results": [], "errors": []}]],
        [[], {"results": [], "errors": []}],
        ["]", {"results": [], "errors": []}],
    ],
    ids=[
        "single-object",
        "mixed-array",
        "nested-array",
        "empty-array",
        "quoted-bracket",
    ],
)
@pytest.mark.parametrize("prefix", ["", "Running semgrep...\n", "[main] INFO start\n"])
@pytest.mark.parametrize("truncated", [False, True], ids=["complete", "truncated"])
def test_check_rejects_wrapped_json_arrays(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    returncode: int,
    entries: list[object],
    prefix: str,
    truncated: bool,
) -> None:
    """A report object inside an array must not be extracted as a clean scan.

    Args:
        semgrep_plugin: Plugin with the version check bypassed.
        tmp_path: Temporary directory for scan input.
        returncode: Successful, findings, or execution error exit status.
        entries: An array containing a report-shaped object.
        prefix: Optional prose or bracketed logging before the array.
        truncated: Whether the array's closing brackets are missing.
    """
    source = tmp_path / "scan.py"
    source.write_text("pass\n", encoding="utf-8")
    array = json.dumps(entries)
    if truncated:
        array = array.rstrip("]")
    report = prefix + array + ("\n[main] INFO done" if prefix else "")
    with patch.object(
        semgrep_plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(returncode, report, "", report),
    ):
        result = semgrep_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(report)


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
