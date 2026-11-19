"""commitlint's inspection claim follows whether the run completed (#3352).

commitlint reads commit messages, not files. A completed run read its input
and reports ``inspected_files=True``; a run that failed without a verdict is
an execution failure with unknown inspection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.commitlint.definition import CommitlintPlugin

# commitlint with a syntax error in commitlint.config.js: exit 1, no stdout.
_BROKEN_CONFIG_STDERR = (
    "/repo/commitlint.config.js:2\n"
    "\n"
    "SyntaxError: Unexpected end of input\n"
    "    at wrapSafe (node:internal/modules/cjs/loader:1486:18)\n"
)

_VIOLATION = (
    "⧗   --- input ---\n"
    "bad message\n"
    "✖   type may not be empty [type-empty]\n"
    "\n"
    "✖   found 1 problems, 0 warnings\n"
)


def _check(
    plugin: CommitlintPlugin,
    tmp_path: Path,
    proc: SubprocessResult,
) -> ToolResult:
    """Run the plugin's check against one mocked commitlint process.

    Args:
        plugin: Plugin under test.
        tmp_path: Working directory for the run.
        proc: The commitlint process to return.

    Returns:
        The plugin's result.
    """
    (tmp_path / "placeholder.txt").write_text("x\n", encoding="utf-8")
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(tmp_path)], {})


def test_failed_run_without_violations_is_incomplete(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """A broken config names commitlint's error and claims no inspection."""
    result = _check(
        commitlint_plugin,
        tmp_path,
        SubprocessResult(
            returncode=1,
            stdout="",
            stderr=_BROKEN_CONFIG_STDERR,
            output=_BROKEN_CONFIG_STDERR,
        ),
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_equal_to(
        "commitlint exited with an error and reported no violations: "
        "/repo/commitlint.config.js:2 | SyntaxError: Unexpected end of input | "
        "at wrapSafe (node:internal/modules/cjs/loader:1486:18)",
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "issues"),
    [(0, "", 0), (1, _VIOLATION, 1)],
    ids=["clean", "violation"],
)
def test_completed_run_reports_inspection(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
    returncode: int,
    stdout: str,
    issues: int,
) -> None:
    """A clean run or a reported violation read the commit message."""
    result = _check(
        commitlint_plugin,
        tmp_path,
        SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            output=stdout,
        ),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(issues)
