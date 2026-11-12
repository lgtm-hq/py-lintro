"""SQLFluff configuration errors are execution failures (#3352).

SQLFluff 4.5.0 with an unknown dialect exits 2 and prints
``Error: Unknown dialect 'bogus'`` on stdout, with nothing on stderr. That
is SQLFluff failing to run, not a malformed JSON report.
"""

from __future__ import annotations

import subprocess  # nosec B404 - TimeoutExpired for a mocked lint run
from pathlib import Path
from unittest.mock import patch

import pytest
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


TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/usr/local/bin/sqlfluff", line 6, in <module>\n'
    "    sys.exit(cli())\n"
    "configparser.MissingSectionHeaderError: File contains no section headers.\n"
    "file: '<string>', line: 1\n"
)
CLEAN_REPORT = '[{"filepath": "query.sql", "violations": []}]'


def _run(returncode: int, stdout: str, stderr: str = "") -> SubprocessResult:
    """Build one canned ``sqlfluff lint`` invocation.

    Args:
        returncode: Exit status.
        stdout: The JSON report stream.
        stderr: The diagnostics stream.

    Returns:
        The subprocess result.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )


@pytest.mark.parametrize(
    ("side_effect", "reason", "message"),
    [
        (
            _run(0, ""),
            "SQLFluff could not run.",
            "SQLFluff printed no report (exit 0).",
        ),
        (
            _run(1, "", TRACEBACK),
            "SQLFluff could not run.",
            "SQLFluff printed no report (exit 1): configparser."
            "MissingSectionHeaderError: File contains no section headers.",
        ),
        (
            _run(2, CLEAN_REPORT),
            "SQLFluff could not run.",
            "sqlfluff lint failed before fix",
        ),
        (
            subprocess.TimeoutExpired(["sqlfluff"], 1),
            "SQLFluff timed out before reporting.",
            "execution failures (including timeouts)",
        ),
    ],
    ids=["empty-exit-zero", "traceback", "exit-2-without-verdict", "timeout"],
)
def test_fix_without_a_lint_verdict_is_unmeasured(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
    side_effect: SubprocessResult | Exception,
    reason: str,
    message: str,
) -> None:
    """No usable report or verdict leaves the fix counts unknown, never zero."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        side_effect=[side_effect],
    ):
        result = sqlfluff_plugin.fix([str(sql)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(reason)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).contains(message).does_not_contain("Traceback")


def test_empty_report_is_tracked_even_past_the_subprocess_override(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """An empty report reaching the parser is still an unmeasured run."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess_result",
        return_value=_run(0, ""),
    ):
        result = sqlfluff_plugin.fix([str(sql)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.output).contains("SQLFluff printed no report.")


def test_malformed_findings_report_is_a_parse_failure_not_a_run_error(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Exit 1 with malformed records is an incomplete report, not a run error."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    malformed = '[{"filepath": "query.sql", "violations": "oops"}]'
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        return_value=_run(1, malformed),
    ):
        result = sqlfluff_plugin.fix([str(sql)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.residual_unknown).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(
        "SQLFluff returned an incomplete report.",
    )
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).contains(
        "SQLFluff printed a report that could not be fully parsed",
    ).does_not_contain("could not run")
