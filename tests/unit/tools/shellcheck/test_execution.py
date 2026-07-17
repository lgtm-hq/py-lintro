"""Unit tests for shellcheck plugin check execution and output parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.shellcheck.shellcheck_parser import parse_shellcheck_output
from lintro.tools.definitions.shellcheck import ShellcheckPlugin
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
