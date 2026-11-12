"""Stylelint runs whose files its own config ignores are "no files" (#3393).

Run with ``--allow-empty-input``, Stylelint 18 prints ``[]`` and exits 0 when
``.stylelintignore`` or ``ignoreFiles`` excludes every file it was given.
Without the flag it exits 1 with ``AllFilesIgnoredError``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
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
    files: list[str] | None = None,
) -> tuple[ToolResult, list[list[str]]]:
    """Run ``check`` or ``fix`` with canned Stylelint invocations.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        runs: ``(exit 0?, combined output)`` for each invocation, in order.
        fix: Run ``fix`` instead of ``check``.
        files: Relative paths passed to Stylelint (default ``["a.css"]``).

    Returns:
        The result and the command of every Stylelint invocation.
    """
    ctx = make_ctx(tmp_path, files or ["a.css"])
    with (
        patch.object(plugin, "prepare", return_value=ctx),
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


# A Stylelint 18 record for a file ``ignoreFiles`` excluded.
IGNORED_RECORD = (
    '{"source":"/repo/a.css","deprecations":[],"invalidOptionWarnings":[],'
    '"parseErrors":[],"errored":false,"warnings":[],"ignored":true}'
)


@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
def test_ignored_records_are_no_files(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
    fix: bool,
) -> None:
    """A report whose every record is ignored lints no file.

    Args:
        stylelint_plugin: The plugin under test.
        tmp_path: Working directory for the context.
        fix: Run ``fix`` instead of ``check``.
    """
    result, commands = _run(
        stylelint_plugin,
        tmp_path,
        [(True, f"[{IGNORED_RECORD}]")],
        fix=fix,
    )

    assert_that(commands).is_length(1)
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.output).is_equal_to(STYLELINT_ALL_IGNORED_MESSAGE)


@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
@pytest.mark.parametrize(
    "warnings",
    [
        pytest.param('"warnings":null', id="null-warnings"),
        pytest.param('"warnings":{}', id="object-warnings"),
        pytest.param('"warnings":"none"', id="string-warnings"),
    ],
)
def test_malformed_ignored_record_is_an_execution_failure(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
    warnings: str,
    fix: bool,
) -> None:
    """An ignored record with malformed ``warnings`` is no verdict.

    Args:
        stylelint_plugin: The plugin under test.
        tmp_path: Working directory for the context.
        warnings: The malformed ``warnings`` member.
        fix: Run ``fix`` instead of ``check``.
    """
    report = f'[{{"source":"/repo/a.css","ignored":true,{warnings}}}]'
    result, _ = _run(stylelint_plugin, tmp_path, [(True, report)], fix=fix)

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_ignored_record_with_invalid_option_keeps_the_finding(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Invalid-option warnings on an ignored record are reported, not dropped."""
    report = (
        '[{"source":"/repo/a.css","ignored":true,"warnings":[],'
        '"invalidOptionWarnings":[{"text":"Invalid option for rule x"}]}]'
    )
    result, _ = _run(stylelint_plugin, tmp_path, [(False, report)])

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues).extracting("message").contains(
        "Invalid option for rule x",
    )


@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
def test_empty_report_for_a_backslash_path_is_an_execution_failure(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
    fix: bool,
) -> None:
    r"""Stylelint reads ``dir\\file.css`` as ``dir/file.css``: not "no files".

    Args:
        stylelint_plugin: The plugin under test.
        tmp_path: Working directory for the context.
        fix: Run ``fix`` instead of ``check``.
    """
    result, commands = _run(
        stylelint_plugin,
        tmp_path,
        [(True, "[]")],
        fix=fix,
        files=["ok.css", "dir\\file.css"],
    )

    assert_that(commands).is_length(1)
    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains("dir\\\\file.css")
