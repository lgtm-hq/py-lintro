"""Tests for TaploPlugin check and fix method execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.taplo import TaploPlugin

# Tests for TaploPlugin.check method


def test_check_with_mocked_subprocess_success(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text('[project]\nname = "test"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = taplo_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_lint_errors(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when taplo lint finds problems.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text("invalid = \n")

    taplo_output = """error[invalid_value]: invalid value
  --> test.toml:1:10
   |
 1 | invalid =
   |          ^ expected a value
"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[(False, taplo_output), (True, "")],
        ),
    ):
        result = taplo_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_mocked_subprocess_format_issues(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when taplo fmt --check finds formatting problems.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text('[project]\nname="test"\n')

    format_output = """error[formatting]: the file is not properly formatted
  --> test.toml:2:1
   |
 2 | name="test"
   | ^ formatting issue
"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[(True, ""), (False, format_output)],
        ),
    ):
        result = taplo_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_toml_files(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no TOML files found.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_toml_file = tmp_path / "test.txt"
    non_toml_file.write_text("Not a TOML file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = taplo_plugin.check([str(non_toml_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


# Tests for TaploPlugin.fix method


def test_fix_with_mocked_subprocess_success(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when formatting applied successfully.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text('[project]\nname="test"\n')

    format_issue_output = """error[formatting]: the file is not properly formatted
  --> test.toml:2:1
   |
 2 | name="test"
   | ^ formatting issue
"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[
                (False, format_issue_output),  # initial format check
                (True, ""),  # lint check
                (True, ""),  # fix command
                (True, ""),  # final format check
                (True, ""),  # final lint check
            ],
        ),
    ):
        result = taplo_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_with_mocked_subprocess_partial_fix(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns partial success when some issues cannot be fixed.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text("invalid = \n")

    format_issue = """error[formatting]: the file is not properly formatted
  --> test.toml:1:1
   |
 1 | invalid =
   | ^ formatting issue
"""
    lint_issue = """error[invalid_value]: invalid value
  --> test.toml:1:10
   |
 1 | invalid =
   |          ^ expected a value
"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[
                (False, format_issue),  # initial format check
                (False, lint_issue),  # initial lint check
                (True, ""),  # fix command
                (True, ""),  # final format check - format is fixed
                (False, lint_issue),  # final lint check - syntax error remains
            ],
        ),
    ):
        result = taplo_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_with_no_changes_needed(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no changes are needed.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.toml"
    test_file.write_text('[project]\nname = "test"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[
                (True, ""),  # initial format check - no issues
                (True, ""),  # initial lint check - no issues
                (True, ""),  # fix command
                (True, ""),  # final format check
                (True, ""),  # final lint check
            ],
        ),
    ):
        result = taplo_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_with_no_toml_files(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no TOML files found.

    Args:
        taplo_plugin: The TaploPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_toml_file = tmp_path / "test.txt"
    non_toml_file.write_text("Not a TOML file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = taplo_plugin.fix([str(non_toml_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No .toml files")
