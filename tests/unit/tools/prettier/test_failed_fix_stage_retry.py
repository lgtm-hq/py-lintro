"""A failed Prettier fix stage stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results. A ``--write`` that exits with
an error, or a launch that raises, measured nothing, so the convergence retry
must stop on it, flagged as an execution failure, rather than let a clean
second pass replace it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_DIRTY = "[warn] test.js\n[warn] Code style issues found."
_CLEAN = SubprocessResult(0, "", "", "")


@pytest.mark.parametrize(
    "failed_pass",
    [
        [
            SubprocessResult(1, _DIRTY, "", _DIRTY),
            SubprocessResult(2, "", "[error] EACCES", "[error] EACCES"),
        ],
        [
            SubprocessResult(1, _DIRTY, "", _DIRTY),
            OSError("EACCES: prettier"),
        ],
    ],
    ids=["write-exits-with-error", "write-cannot-launch"],
)
def test_failed_stage_then_clean_retry_stays_unmeasured(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
    failed_pass: list[SubprocessResult | Exception],
) -> None:
    """The failure survives a clean second pass with unknown fix counts."""
    with (
        patch.object(
            prettier_plugin,
            "_prepare_execution",
            return_value=mock_execution_context_for_tool(
                files=["test.js"],
                rel_files=["test.js"],
                cwd="/tmp",
            ),
        ),
        patch.object(
            prettier_plugin,
            "_get_executable_command",
            return_value=["prettier"],
        ),
        patch.object(prettier_plugin, "_build_config_args", return_value=[]),
        patch.object(
            prettier_plugin,
            "_run_subprocess_result",
            side_effect=[*failed_pass, _CLEAN, _CLEAN, _CLEAN],
        ),
    ):
        result = run_fix_with_retry(prettier_plugin, ["/tmp/test.js"], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("file").is_equal_to(["test.js"])
    assert_that(result.output).contains("EACCES")
