"""Tests for SqlfluffPlugin.fix method initial_issues population."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.sqlfluff.definition import SqlfluffPlugin
from tests.test_samples_helpers import copy_sample

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
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

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
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_clean.sql",
        dest_name=test_file.name,
    )

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_none()


def test_fix_preserves_initial_issues_for_the_verify_pass(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix records what it saw before fixing, which the verify pass needs.

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

    del remaining_output

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=[
            (False, SQLFLUFF_LINT_OUTPUT_WITH_ISSUES),  # Initial lint check
            (True, "Fixed 1 file(s)"),  # Fix command
        ],
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    # No third subprocess call: since #1743 sqlfluff does not re-lint itself,
    # so a successful fix reports everything it saw as fixed and the run-level
    # verify pass supplies the authoritative residual.
    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_length(1)
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_reports_everything_as_remaining_when_it_exits_nonzero(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero sqlfluff fix reports every initial issue as remaining.

    sqlfluff's fix command can apply some fixes and still exit non-zero if
    other rules are unfixable. Since #1743 the plugin no longer re-lints to
    resolve that ambiguity: the run-level verify pass measures the residual
    once, after every mutating tool has run, and over-reporting here is the
    safe direction until it does.

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

    two_issues_output = """[
        {
            "filepath": "test_query.sql",
            "violations": [
                {
                    "start_line_no": 1, "start_line_pos": 1,
                    "end_line_no": 1, "end_line_pos": 6,
                    "code": "L010", "description": "kw",
                    "name": "capitalisation.keywords"
                },
                {
                    "start_line_no": 1, "start_line_pos": 10,
                    "end_line_no": 1, "end_line_pos": 11,
                    "code": "L030", "description": "fn",
                    "name": "capitalisation.functions"
                }
            ]
        }
    ]"""
    one_remaining_output = """[
        {
            "filepath": "test_query.sql",
            "violations": [
                {
                    "start_line_no": 1, "start_line_pos": 10,
                    "end_line_no": 1, "end_line_pos": 11,
                    "code": "L030", "description": "fn",
                    "name": "capitalisation.functions"
                }
            ]
        }
    ]"""

    del one_remaining_output

    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=[
            (False, two_issues_output),  # Initial lint: 2 issues
            (False, "Unfixable rule"),  # Fix: exits non-zero
        ],
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(2)
