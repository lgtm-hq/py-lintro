"""Unit tests for sqlfluff plugin check and fix method execution."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only constructing mocked process results
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.sqlfluff.definition import SqlfluffPlugin
from tests.test_samples_helpers import copy_sample

# Tests for SqlfluffPlugin.check method


@pytest.mark.parametrize("operation", ["check", "fix"])
@pytest.mark.parametrize("message", [None, "kept"])
def test_lint_json_is_not_poisoned_by_stderr(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
    operation: str,
    message: str | None,
) -> None:
    """The real subprocess boundary keeps stderr out of SQLFluff's report."""
    test_file = tmp_path / "query.sql"
    test_file.write_text("select 1;\n")
    violations = [{"description": message}] if message else []
    stdout = json.dumps([{"filepath": str(test_file), "violations": violations}])

    def completed(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Return the lint JSON or the fix command's normal progress output.

        Args:
            args: Actual command assembled by SQLFluff's plugin.
            **_kwargs: Subprocess settings unused by this stub.

        Returns:
            Separated stdout and stderr for the requested phase.
        """
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=stdout if args[1] == "lint" else "Fixed 1 file(s)\n",
            stderr="\nWARNING unrelated configuration notice\n",
        )

    with patch("subprocess.run", side_effect=completed):
        result = getattr(sqlfluff_plugin, operation)([str(test_file)], {})

    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.skipped).is_false()
    if operation == "check":
        assert_that(result.success).is_equal_to(message is None)
        assert_that(result.issues).extracting("message").is_equal_to(
            [message] if message else [],
        )
    else:
        assert_that(result.success).is_true()
        assert_that(result.initial_issues).extracting("message").is_equal_to(
            [message] if message else [],
        )


def test_check_with_mocked_subprocess_success(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_clean.sql",
        dest_name=test_file.name,
    )

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ):
        result = sqlfluff_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when sqlfluff finds problems.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

    sqlfluff_output = """[
        {
            "filepath": "test_query.sql",
            "violations": [
                {
                    "start_line_no": 1,
                    "start_line_pos": 1,
                    "end_line_no": 1,
                    "end_line_pos": 6,
                    "code": "L010",
                    "description": "Keywords must be upper case.",
                    "name": "capitalisation.keywords"
                }
            ]
        }
    ]"""

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(False, sqlfluff_output),
    ):
        result = sqlfluff_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_sql_files(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no SQL files found.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_sql_file = tmp_path / "test.txt"
    non_sql_file.write_text("Not a SQL file")

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    result = sqlfluff_plugin.check([str(non_sql_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


# Tests for SqlfluffPlugin.fix method


def test_fix_with_mocked_subprocess_success(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when fixes applied.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

    sqlfluff_lint_output = """[
        {
            "filepath": "test_query.sql",
            "violations": [
                {
                    "start_line_no": 1,
                    "start_line_pos": 1,
                    "end_line_no": 1,
                    "end_line_pos": 6,
                    "code": "L010",
                    "description": "Keywords must be upper case.",
                    "name": "capitalisation.keywords"
                }
            ]
        }
    ]"""

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=[
            (False, sqlfluff_lint_output),  # Initial lint check - issues found
            (True, "Fixed 1 file(s)"),  # Fix command
            (True, "[]"),  # Verification lint - no issues
        ],
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(1)


def test_fix_with_mocked_subprocess_no_changes(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no changes needed.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_clean.sql",
        dest_name=test_file.name,
    )

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    # Initial lint check finds no issues, so fix and verify are skipped
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()


def test_fix_with_no_sql_files(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no SQL files found.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_sql_file = tmp_path / "test.txt"
    non_sql_file.write_text("Not a SQL file")

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    result = sqlfluff_plugin.fix([str(non_sql_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")
