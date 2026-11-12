"""html-validate runs whose files its own config ignores are "no files" (#3393).

When ``.htmlvalidateignore`` excludes every file it was given, html-validate
11.21 prints nothing to stdout, writes ``No files matching patterns [...]``
to stderr and exits 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate.definition import (
    HTML_VALIDATE_ALL_IGNORED_MESSAGE,
    HtmlValidatePlugin,
)

NO_FILES_MATCHING = "No files matching patterns [ 'index.html' ]\n"


def _check(
    plugin: HtmlValidatePlugin,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> ToolResult:
    """Run ``check`` on one HTML file with a canned html-validate run.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        returncode: html-validate's exit status.
        stdout: The JSON report stream.
        stderr: The diagnostics stream.

    Returns:
        The check result.
    """
    ctx = MagicMock(should_skip=False, early_result=None, timeout=30)
    ctx.cwd = str(tmp_path)
    ctx.files = ctx.rel_files = ["index.html"]
    run = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with (
        patch.object(plugin, "_prepare_execution", return_value=ctx),
        patch.object(
            plugin,
            "_get_executable_command",
            return_value=["/pinned/html-validate"],
        ),
        patch.object(plugin, "_run_subprocess_result", return_value=run),
    ):
        return plugin.check([str(tmp_path)], {})


def test_all_ignored_is_no_files_without_inspection(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """An unmatched-patterns error with no report is "no files", not a failure."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=1,
        stdout="",
        stderr=NO_FILES_MATCHING,
    )

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.output).is_equal_to(HTML_VALIDATE_ALL_IGNORED_MESSAGE)


def test_other_error_without_report_still_fails(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A failure that does not name unmatched patterns stays a failure."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=1,
        stdout="",
        stderr="Error: Failed to load configuration from .htmlvalidate.json\n",
    )

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()


def test_no_files_matching_beside_a_report_is_not_no_files(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A printed report wins over the stderr phrase: the run inspected files."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=0,
        stdout="[]",
        stderr=NO_FILES_MATCHING,
    )

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
