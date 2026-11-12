"""SQLFluff relies on the per-file runner for failed lint passes (#3401).

SQLFluff no longer tracks its own lint errors. A lint pass that could not run
must still leave the fix run unmeasured, and a clean convergence retry must
not replace it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.sqlfluff.definition import SqlfluffPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_CLEAN = '[{"filepath": "query.sql", "violations": []}]'


def test_failed_lint_then_clean_retry_stays_unmeasured(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A lint pass that exited 2 is not replaced by a clean second pass."""
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT 1\n")
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(2, _CLEAN, "", _CLEAN),
            SubprocessResult(0, _CLEAN, "", _CLEAN),
        ],
    ):
        result = run_fix_with_retry(sqlfluff_plugin, [str(sql)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to("SQLFluff could not run.")
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).contains("sqlfluff lint failed before fix")


_VIOLATION = (
    '[{"filepath": "query.sql", "violations": [{"start_line_no": 1, '
    '"start_line_pos": 7, "code": "LT01", "description": "Extra space.", '
    '"name": "layout.spacing"}]}]'
)
_PERMISSION_TRACEBACK = (
    "Traceback (most recent call last):\n"
    "PermissionError: [Errno 13] Permission denied: 'query.sqlmj6tiuro.sql'\n"
)


def test_failed_write_then_clean_retry_stays_unmeasured(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """``sqlfluff fix`` exit 1 with a PermissionError is not a findings exit."""
    sql = tmp_path / "query.sql"
    sql.write_text("select  1\n")
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, _VIOLATION, "", _VIOLATION),
            SubprocessResult(1, "", _PERMISSION_TRACEBACK, _PERMISSION_TRACEBACK),
            SubprocessResult(0, _CLEAN, "", _CLEAN),
        ],
    ):
        result = run_fix_with_retry(sqlfluff_plugin, [str(sql)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(
        "SQLFluff could not write its fixes.",
    )
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.issues).extracting("code").is_equal_to(["LT01"])


def test_unfixable_violations_exit_stays_measured(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """``sqlfluff fix`` exit 1 for unfixable violations is a measured result."""
    sql = tmp_path / "query.sql"
    sql.write_text("select  1\n")
    unfixable = "== [query.sql] FAIL\nL:   1 | P:   7 | LT01 | Extra space.\n"
    with patch.object(
        BaseToolPlugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, _VIOLATION, "", _VIOLATION),
            SubprocessResult(1, unfixable, "", unfixable),
        ],
    ):
        result = sqlfluff_plugin.fix([str(sql)], {})

    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_reason_names_a_run_failure_beside_a_malformed_report(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """One file that could not run and one malformed report name both."""
    broken = tmp_path / "a.sql"
    malformed = tmp_path / "b.sql"
    for sql in (broken, malformed):
        sql.write_text("SELECT 1\n")
    bad_report = '[{"filepath": "b.sql", "violations": "oops"}]'

    def respond(cmd: list[str], *_args: object) -> SubprocessResult:
        if cmd[-1].endswith("a.sql"):
            dialect = "Error: Unknown dialect 'bogus'\n"
            return SubprocessResult(2, dialect, "", dialect)
        return SubprocessResult(1, bad_report, "", bad_report)

    with patch.object(BaseToolPlugin, "_run_subprocess_result", side_effect=respond):
        result = sqlfluff_plugin.fix([str(broken), str(malformed)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.residual_unknown_reason).is_equal_to(
        "SQLFluff could not run. SQLFluff returned an incomplete report.",
    )
