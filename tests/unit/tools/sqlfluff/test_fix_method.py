"""Tests for SqlfluffPlugin.fix method initial_issues population."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.sqlfluff import SqlfluffPlugin

SQLFLUFF_LINT_OUTPUT_WITH_ISSUES = """[
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


def test_fix_populates_initial_issues(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix populates initial_issues when issues are found and fixed.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    test_file.write_text("select * from users;\n")

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=[
            (False, SQLFLUFF_LINT_OUTPUT_WITH_ISSUES),  # Initial lint check
            (True, "Fixed 1 file(s)"),  # Fix command
            (True, "[]"),  # Verification lint - no issues
        ],
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(1)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_initial_issues_none_when_no_issues(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix sets initial_issues to None when no issues detected.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    test_file.write_text("SELECT * FROM users;\n")

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_none()


def test_fix_partial_fix_preserves_initial_issues(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix preserves initial_issues when some issues remain after fix.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    test_file.write_text("select * from users;\n")

    remaining_output = """[
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

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=[
            (False, SQLFLUFF_LINT_OUTPUT_WITH_ISSUES),  # Initial lint check
            (True, "Fixed 1 file(s)"),  # Fix command
            (False, remaining_output),  # Verification - issue remains
        ],
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(1)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(1)
