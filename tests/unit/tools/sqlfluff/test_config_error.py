"""SQLFluff configuration errors are execution failures (#3352).

SQLFluff 4.5.0 with an unknown dialect exits 2 and prints
``Error: Unknown dialect 'bogus'`` on stdout, with nothing on stderr. That
is SQLFluff failing to run, not a malformed JSON report.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.sqlfluff.definition import SqlfluffPlugin

UNKNOWN_DIALECT = SubprocessResult(
    returncode=2,
    stdout="Error: Unknown dialect 'bogus'\n",
    stderr="",
    output="Error: Unknown dialect 'bogus'\n",
)
REASON = "SQLFluff could not run (exit 2): Error: Unknown dialect 'bogus'"


def test_check_names_the_configuration_error_without_a_parse_failure(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """The error is named once and is not counted as a malformed report."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        return_value=UNKNOWN_DIALECT,
    ):
        result = sqlfluff_plugin.check([str(sql)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).contains(REASON).does_not_contain("Malformed")
    assert_that(str(result.output).count("Unknown dialect")).is_equal_to(1)


def test_fix_leaves_accounting_unknown_after_a_configuration_error(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A lint pass that could not run measured nothing to fix or keep."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        return_value=UNKNOWN_DIALECT,
    ):
        result = sqlfluff_plugin.fix([str(sql)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to("SQLFluff could not run.")
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.output).contains(REASON)
