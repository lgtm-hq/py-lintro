"""Tests with real RuffPlugin instance."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - constructs a timeout exception only
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.tools.ruff.check import execute_ruff_check
from tests.unit.tools.subprocess_helpers import subprocess_result

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


@pytest.mark.parametrize("output", ["[{}]", '{"filename":'])
def test_ruff_parse_warning_names_rejected_records(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    loguru_records: list[tuple[str, str]],
    output: str,
) -> None:
    """A zero-exit rejected report is diagnosed as a parse failure."""
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
        return_value=subprocess_result(True, output),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    warnings = "\n".join(
        message for level, message in loguru_records if level == "WARNING"
    )
    assert_that(result.success).is_false()
    assert_that(warnings).contains(
        "1 diagnostic record(s) could not be parsed",
    )
    assert_that(warnings).does_not_contain("subprocess failed")


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    ("output", "expected_issues"),
    [
        ("[{}]", 0),
        ("[null,", 0),
        ('[{"filename": "a.py"}]', 0),
        ('{}\n{"filename": "a.py", "message": "bad syntax", "code": null}', 1),
        ('{"filename":', 0),
        ('{"filename":\n{"file": "a.py", "message": "bad syntax", "code": null}', 1),
        ("null", 0),
        (" \nnull\t", 0),
        ("0", 0),
        ("42", 0),
        ("-3.5", 0),
        ('""', 0),
        ('"diagnostics"', 0),
        ("true", 0),
        ("false", 0),
    ],
)
def test_ruff_check_counts_rejected_records(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    success: bool,
    output: str,
    expected_issues: int,
) -> None:
    """Malformed records are counted even when the subprocess exits zero."""
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
        return_value=subprocess_result(success, output),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_equal_to(expected_issues)
    assert_that(result.issues).is_length(expected_issues)
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()


@pytest.mark.parametrize("code", [None, "F401"])
def test_ruff_check_keeps_valid_diagnostic_with_optional_code(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    code: str | None,
) -> None:
    """An optional rule code, null or set, keeps a complete diagnostic array."""
    output = json.dumps(
        [
            {
                "filename": temp_python_file,
                "message": "diagnostic retained",
                "location": {"row": 1, "column": 1},
                "code": code,
            },
        ],
    )
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
        return_value=subprocess_result(False, output),
    ):
        result = ruff_plugin.check([temp_python_file], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("file", "message", "code").is_equal_to(
        [(temp_python_file, "diagnostic retained", code or "")],
    )
    assert_that(result.execution_failed).is_false()
    assert_that(result.findings_complete).is_true()
    assert_that(result.inspected_files).is_true()


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("warning_on_stderr", [False, True])
@pytest.mark.parametrize(
    "warning",
    [
        "warning: stray output\n",
        "warning: cache path [cache]/ruff is unavailable\n",
        "warning: The top-level linter settings are deprecated in `project[cache]`:\n"
        "  - 'ignore' -> 'lint.ignore'\n"
        "  - 'select' -> 'lint.select'\n",
    ],
)
@pytest.mark.parametrize(
    ("report", "expected_codes", "native_array"),
    [
        ("null", [], False),
        ("0", [], False),
        ("42", [], False),
        ("-3.5", [], False),
        ('""', [], False),
        ('"diagnostics"', [], False),
        ("true", [], False),
        ("false", [], False),
        ("[]", [], True),
        (" \n[]\n", [], True),
        ("[\n]", [], True),
        ("array", [""], True),
        ("object", [""], False),
        ("json-lines", ["", "F401"], False),
    ],
)
def test_ruff_check_warning_stream_controls_report_certification(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    report: str,
    expected_codes: list[str],
    native_array: bool,
    warning_on_stderr: bool,
    returncode: int,
    warning: str,
) -> None:
    """Keep recovered findings while only certifying complete native stdout."""
    diagnostic = {
        "filename": temp_python_file,
        "message": "Expected expression",
        "location": {"row": 1, "column": 1},
        "code": None,
    }
    reports = {
        "array": json.dumps([diagnostic], indent=2),
        "object": json.dumps(diagnostic),
        "json-lines": json.dumps(diagnostic)
        + "\n"
        + json.dumps({**diagnostic, "message": "unused import", "code": "F401"}),
    }
    stdout = reports.get(report, report)
    stderr = warning if warning_on_stderr else ""
    if not warning_on_stderr:
        stdout += "\n" + warning
    process = SubprocessResult(returncode, stdout, stderr, stdout + "\n" + stderr)
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
        return_value=process,
    ):
        result = ruff_plugin.check([temp_python_file], {})

    valid_report = native_array and warning_on_stderr
    complete = valid_report and (returncode == 0 or bool(expected_codes))
    assert_that(result.parse_failures_count).is_equal_to(0 if valid_report else 1)
    assert_that(result.issues_count).is_equal_to(len(expected_codes))
    assert_that(result.issues).extracting("file", "code").is_equal_to(
        [(temp_python_file, code) for code in expected_codes],
    )
    assert_that(result.success).is_equal_to(
        complete and returncode == 0 and not expected_codes,
    )
    assert_that(result.findings_complete).is_equal_to(complete)
    assert_that(result.execution_failed).is_equal_to(not complete)
    assert_that(result.inspected_files).is_equal_to(True if complete else None)
    assert_that(result.output).is_equal_to(None if complete else process.output)


def test_ruff_parse_failure_survives_format_timeout(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """A later formatter timeout retains the rejected lint record count."""
    ruff_plugin.options["format_check"] = True
    with patch(
        "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
        side_effect=[
            subprocess_result(True, "[{}]"),
            subprocess.TimeoutExpired(cmd=["ruff", "format"], timeout=30),
        ],
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    assert_that(result.timed_out).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_zero()
    assert_that(result.success).is_false()


def test_execute_ruff_check_with_real_plugin_no_files(
    ruff_plugin: RuffPlugin,
) -> None:
    """Execute with real plugin when no files to check.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
    """
    with patch.object(ruff_plugin, "_verify_tool_version", return_value=None):
        result = execute_ruff_check(ruff_plugin, [])

        assert_that(result.success).is_true()
        assert_that(result.output).is_equal_to("No files to check.")


@pytest.mark.parametrize(
    ("output", "expected_success", "expected_failures"),
    [
        ("", False, 1),
        (" \n\t", False, 1),
        ("[]", True, 0),
        (" \n[]\n", True, 0),
        ("[\n]", True, 0),
    ],
)
def test_execute_ruff_check_with_real_plugin(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    output: str,
    expected_success: bool,
    expected_failures: int,
) -> None:
    """Execute with real plugin and temp file.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
        temp_python_file: Temporary Python file for testing.
        output: Native lint report envelope from Ruff.
        expected_success: Whether a native JSON array was received.
        expected_failures: Number of malformed report envelopes.
    """
    with (
        patch.object(ruff_plugin, "_verify_tool_version", return_value=None),
        patch(
            "lintro.tools.ruff.check.run_subprocess_result_with_timeout",
            return_value=subprocess_result(True, output),
        ),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

        assert_that(result.success).is_equal_to(expected_success)
        assert_that(result.name).is_equal_to(ToolName.RUFF)
        assert_that(result.parse_failures_count).is_equal_to(expected_failures)
        assert_that(result.issues_count).is_zero()
