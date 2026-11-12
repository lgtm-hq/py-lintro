"""A Prettier write that times out is never reported as measured (#3352).

The real plugin runs a canned dirty ``--check`` and then a ``--write`` that
times out. Nothing verified what remains, so the convergence retry must report
unknown fix counts whether or not retries are enabled.
"""

from __future__ import annotations

import subprocess  # nosec B404 - TimeoutExpired for a mocked prettier run
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

DIRTY = "[warn] test.js\n[warn] Code style issues found."


@pytest.mark.parametrize("max_retries", [1, 3], ids=["no-retry", "retries"])
def test_write_timeout_leaves_fix_counts_unknown(
    prettier_plugin: PrettierPlugin,
    mock_execution_context_for_tool: Any,
    max_retries: int,
) -> None:
    """The detected finding survives; fixed and remaining stay unknown."""
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
            side_effect=[
                SubprocessResult(1, DIRTY, "", DIRTY),
                subprocess.TimeoutExpired(cmd="prettier", timeout=30),
            ],
        ),
    ):
        result = run_fix_with_retry(
            prettier_plugin,
            ["/tmp/test.js"],
            {},
            max_retries,
        )

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).extracting("file", "message").is_equal_to(
        [("test.js", "Code style issues found")],
    )
