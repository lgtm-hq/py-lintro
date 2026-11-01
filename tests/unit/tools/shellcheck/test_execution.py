"""Unit tests for shellcheck plugin check execution and output parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import Checked
from lintro.parsers.shellcheck.shellcheck_parser import parse_shellcheck_output
from lintro.tools.shellcheck.definition import ShellcheckPlugin
from lintro.utils.json_output import serialize_tool_result
from tests.test_samples_helpers import copy_sample

# Tests for ShellcheckPlugin.check method


def test_check_with_mocked_subprocess_success(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a test shell file
    test_file = copy_sample(
        tmp_path,
        "tools",
        "shell",
        "shellcheck",
        "shellcheck_hello_world.sh",
        dest_name="test_script.sh",
    )

    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ):
        result = shellcheck_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when shellcheck finds problems.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "shell",
        "shellcheck",
        "shellcheck_unquoted_var.sh",
        dest_name="test_script.sh",
    )

    shellcheck_output = """[
        {
            "file": "test_script.sh",
            "line": 2,
            "endLine": 2,
            "column": 6,
            "endColumn": 10,
            "level": "warning",
            "code": 2086,
            "message": "Double quote to prevent globbing and word splitting."
        }
    ]"""

    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        return_value=(False, shellcheck_output),
    ):
        result = shellcheck_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize(
    ("output", "malformed"),
    [
        pytest.param('{"comments": null}', True, id="null-comments"),
        pytest.param('{"comments": false}', True, id="boolean-comments"),
        pytest.param('{"comments": 0}', True, id="number-comments"),
        pytest.param('{"comments": ""}', True, id="empty-string-comments"),
        pytest.param('{"comments": "not a list"}', True, id="string-comments"),
        pytest.param('{"comments": {}}', True, id="empty-object-comments"),
        pytest.param(
            '{"comments": {"file": "script.sh", "message": "warning"}}',
            True,
            id="object-comments",
        ),
        pytest.param("{}", True, id="missing-comments"),
        pytest.param('{"other": []}', True, id="unrelated-field"),
        pytest.param("null", True, id="null-report"),
        pytest.param("false", True, id="boolean-report"),
        pytest.param("0", True, id="number-report"),
        pytest.param('""', True, id="string-report"),
        pytest.param("not valid json", True, id="invalid-json"),
        pytest.param(
            '{"comments": []}\nshellcheck: unexpected stderr',
            True,
            id="trailing-noise",
        ),
        pytest.param('{"comments": []}', False, id="empty-json1"),
        pytest.param("[]", False, id="empty-legacy"),
        pytest.param("", False, id="empty-output"),
        pytest.param(" \n ", False, id="whitespace-output"),
    ],
)
def test_check_report_container_verdict(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
    output: str,
    malformed: bool,
    returncode: int,
) -> None:
    """Malformed reports fail with parse evidence at either process exit code."""
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        return_value=(returncode == 0, output),
    ):
        result = shellcheck_plugin.check([str(script)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.timed_out).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count or 0).is_equal_to(int(malformed))
    expected_success = returncode == 0 and not malformed
    assert_that(result.success).is_equal_to(expected_success)
    assert_that(result.outcome.execution).is_equal_to(
        Checked(success=expected_success, inspected_files=result.inspected_files),
    )
    if not malformed:
        assert_that(result.parse_failures_count).is_none()
        serialized = serialize_tool_result(result, action=Action.CHECK)
        assert_that(serialized).does_not_contain_key("parse_failures_count")


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize(
    ("output", "reason", "context"),
    [
        pytest.param(
            "shellcheck: unknown option",
            "invalid JSON",
            "shellcheck: unknown option",
            id="execution-diagnostic",
        ),
        pytest.param(
            '{"comments": null}',
            "expected a list report or an object with a comments list",
            '{"comments": null}',
            id="malformed-container",
        ),
        pytest.param(
            "shellcheck: unknown option\n" + "x" * 500 + "\nSee shellcheck --help",
            "invalid JSON",
            "See shellcheck --help",
            id="long-execution-diagnostic",
        ),
        pytest.param(
            '{"comments": [], "padding": "' + "x" * 500 + '"}\nstderr: failed to read',
            "invalid JSON",
            "stderr: failed to read",
            id="noise-after-long-report",
        ),
    ],
)
def test_check_rejected_report_preserves_bounded_diagnostics(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
    output: str,
    reason: str,
    context: str,
    returncode: int,
) -> None:
    """Rejected reports fail closed with a reason and bounded raw context."""
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        return_value=(returncode == 0, output),
    ):
        result = shellcheck_plugin.check([str(script)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.outcome.execution).is_equal_to(
        Checked(success=False, inspected_files=None),
    )
    assert_that(result.output).contains(reason, context)
    assert_that(result.output).does_not_contain("x" * 201)


@pytest.mark.parametrize("malformed_count", [0, 1, 2])
def test_check_accumulates_parse_failures_per_run(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
    malformed_count: int,
) -> None:
    """Aggregate report failures across files without leaking into later checks."""
    scripts = [tmp_path / "first.sh", tmp_path / "second.sh"]
    for script in scripts:
        script.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
    reports = ['{"comments": null}'] * malformed_count + [
        '{"comments": []}',
    ] * (len(scripts) - malformed_count)
    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        side_effect=[(True, report) for report in reports],
    ):
        result = shellcheck_plugin.check([str(script) for script in scripts], {})

    assert_that(result.parse_failures_count).is_equal_to(malformed_count or None)
    assert_that(result.success).is_equal_to(malformed_count == 0)
    assert_that(result.issues_count).is_zero()
    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        return_value=(True, '{"comments": []}'),
    ):
        clean_result = shellcheck_plugin.check([str(scripts[0])], {})
    assert_that(clean_result.parse_failures_count).is_none()
    assert_that(clean_result.success).is_true()


def test_check_with_no_shell_files(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no shell files found.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_shell_file = tmp_path / "test.txt"
    non_shell_file.write_text("Not a shell file")

    result = shellcheck_plugin.check([str(non_shell_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


# Tests for ShellcheckPlugin.fix method


def test_fix_raises_not_implemented(shellcheck_plugin: ShellcheckPlugin) -> None:
    """Fix raises NotImplementedError.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="Shellcheck cannot automatically"):
        shellcheck_plugin.fix([], {})


# Tests for output parsing


def test_parse_shellcheck_output_single_issue() -> None:
    """Parse single issue from shellcheck output."""
    output = """[
        {
            "file": "test.sh",
            "line": 10,
            "endLine": 10,
            "column": 5,
            "endColumn": 10,
            "level": "warning",
            "code": 2086,
            "message": "Double quote to prevent globbing and word splitting."
        }
    ]"""
    issues = parse_shellcheck_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("test.sh")
    assert_that(issues[0].line).is_equal_to(10)
    assert_that(issues[0].code).is_equal_to("SC2086")
    assert_that(issues[0].message).contains("Double quote")


def test_parse_shellcheck_output_multiple_issues() -> None:
    """Parse multiple issues from shellcheck output."""
    output = """[
        {
            "file": "test.sh",
            "line": 10,
            "column": 5,
            "level": "warning",
            "code": 2086,
            "message": "Double quote to prevent globbing."
        },
        {
            "file": "test.sh",
            "line": 20,
            "column": 1,
            "level": "error",
            "code": 1091,
            "message": "Not following sourced file."
        }
    ]"""
    issues = parse_shellcheck_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].code).is_equal_to("SC2086")
    assert_that(issues[1].code).is_equal_to("SC1091")


def test_parse_shellcheck_output_empty() -> None:
    """Parse empty output returns empty list."""
    issues = parse_shellcheck_output("[]")

    assert_that(issues).is_empty()


def test_parse_shellcheck_output_none() -> None:
    """Parse None output returns empty list."""
    issues = parse_shellcheck_output(None)

    assert_that(issues).is_empty()


def test_parse_shellcheck_output_invalid_json() -> None:
    """Parse invalid JSON returns empty list."""
    issues = parse_shellcheck_output("not valid json")

    assert_that(issues).is_empty()
