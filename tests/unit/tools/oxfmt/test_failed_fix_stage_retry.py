"""A failed oxfmt write stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results: a dirty ``--list-different``,
then a ``--write`` that fails. The convergence retry must stop on that
failure, flagged as one, rather than let a clean pass replace it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.oxfmt.definition import OxfmtPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry


def test_failed_write_then_clean_retry_stays_unmeasured(
    oxfmt_plugin: OxfmtPlugin,
    mock_execution_context_for_tool: Any,
) -> None:
    """The failed write keeps its finding and unknown counts."""
    with (
        patch.object(
            oxfmt_plugin,
            "_prepare_execution",
            return_value=mock_execution_context_for_tool(
                files=["test.js"],
                rel_files=["test.js"],
                cwd="/tmp",
            ),
        ),
        patch.object(oxfmt_plugin, "_get_executable_command", return_value=["oxfmt"]),
        patch.object(oxfmt_plugin, "_build_config_args", return_value=[]),
        patch.object(
            oxfmt_plugin,
            "_run_subprocess_result",
            side_effect=[
                SubprocessResult(1, "test.js", "", "test.js"),
                SubprocessResult(2, "", "EACCES: test.js", "EACCES: test.js"),
                SubprocessResult(0, "", "", ""),
                SubprocessResult(0, "", "", ""),
                SubprocessResult(0, "", "", ""),
            ],
        ),
    ):
        result = run_fix_with_retry(oxfmt_plugin, ["/tmp/test.js"], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).extracting("file").is_equal_to(["test.js"])
    assert_that(result.output).contains("EACCES")
