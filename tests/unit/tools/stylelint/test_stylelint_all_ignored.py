"""Stylelint runs whose files its own config ignores are "no files" (#3393).

Run with ``--allow-empty-input``, Stylelint 18 prints ``[]`` and exits 0 when
``.stylelintignore`` or ``ignoreFiles`` excludes every file it was given.
Without the flag it exits 1 with ``AllFilesIgnoredError``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.stylelint.definition import (
    STYLELINT_ALL_IGNORED_MESSAGE,
    StylelintPlugin,
)
from tests.unit.tools.stylelint.conftest import make_ctx


def _run(
    plugin: StylelintPlugin,
    tmp_path: Path,
    runs: list[tuple[bool, str]],
    *,
    fix: bool = False,
) -> tuple[ToolResult, list[list[str]]]:
    """Run ``check`` or ``fix`` with canned Stylelint invocations.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        runs: ``(exit 0?, combined output)`` for each invocation, in order.
        fix: Run ``fix`` instead of ``check``.

    Returns:
        The result and the command of every Stylelint invocation.
    """
    with (
        patch.object(plugin, "prepare", return_value=make_ctx(tmp_path, ["a.css"])),
        patch.object(plugin, "_get_executable_command", return_value=["stylelint"]),
        patch.object(plugin, "_run_subprocess", side_effect=runs) as run,
    ):
        method = plugin.fix if fix else plugin.check
        result = method([str(tmp_path / "a.css")], {})
    return result, [call.kwargs["cmd"] for call in run.call_args_list]


def test_check_all_ignored_is_no_files_without_inspection(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """An empty report at exit 0 is "no files", never an inspected pass."""
    result, commands = _run(stylelint_plugin, tmp_path, [(True, "[]")])

    assert_that(commands[0]).contains("--allow-empty-input")
    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).is_equal_to(STYLELINT_ALL_IGNORED_MESSAGE)


def test_fix_all_ignored_stops_before_fixing(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A fix run whose initial check names no file runs no ``--fix`` pass."""
    result, commands = _run(stylelint_plugin, tmp_path, [(True, "[]")], fix=True)

    assert_that(commands).is_length(1)
    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_zero()
    assert_that(result.output).is_equal_to(STYLELINT_ALL_IGNORED_MESSAGE)


def test_check_empty_report_at_failed_exit_still_fails(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """An empty report from a run that exited non-zero is not "no files"."""
    result, _ = _run(stylelint_plugin, tmp_path, [(False, "[]")])

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()
