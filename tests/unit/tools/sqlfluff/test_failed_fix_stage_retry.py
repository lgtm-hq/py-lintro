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
