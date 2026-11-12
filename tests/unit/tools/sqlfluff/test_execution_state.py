"""SQLFluff keeps lint diagnostics per execution (#3352).

The registry caches one plugin instance and every invocation runs on a
shallow ``copy_for_execution`` copy, so diagnostics held on the instance must
not be shared between copies running at the same time.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.sqlfluff.definition import SqlfluffPlugin


def test_concurrent_executions_keep_their_own_diagnostics(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A run that starts and ends inside another cannot clear or leak into it."""
    first_sql = tmp_path / "first.sql"
    second_sql = tmp_path / "second.sql"
    for sql in (first_sql, second_sql):
        sql.write_text("SELECT 1\n")
    first = sqlfluff_plugin.copy_for_execution()
    second = sqlfluff_plugin.copy_for_execution()
    results: dict[str, ToolResult] = {}

    def fake_run(cmd: list[str], *_args: object) -> SubprocessResult:
        name = Path(cmd[-1]).stem
        if name == "first" and "second" not in results:
            # The second execution runs to completion while the first is
            # still waiting on its own lint invocation.
            results["second"] = second.check([str(second_sql)], {})
        return SubprocessResult(
            returncode=2,
            stdout="",
            stderr=f"boom-{name}",
            output=f"boom-{name}",
        )

    with patch.object(BaseToolPlugin, "_run_subprocess_result", side_effect=fake_run):
        results["first"] = first.check([str(first_sql)], {})

    assert_that(results["first"].output).contains("boom-first").does_not_contain(
        "boom-second",
    )
    assert_that(results["second"].output).contains("boom-second").does_not_contain(
        "boom-first",
    )
    assert_that(results["first"].execution_failed).is_true()
    assert_that(results["first"].parse_failures_count).is_zero()
