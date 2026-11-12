"""html-validate runs whose files its own config ignores are "no files" (#3393).

When ``.htmlvalidateignore`` excludes every file it was given, html-validate
11.21 prints nothing to stdout, writes ``No files matching patterns [...]``
to stderr and exits 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate.definition import (
    HTML_VALIDATE_ALL_IGNORED_MESSAGE,
    HtmlValidatePlugin,
    ignored_every_file,
)

NO_FILES_MATCHING = "No files matching patterns [ 'index.html' ]\n"


def _check(
    plugin: HtmlValidatePlugin,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: str,
    stderr: str,
    file: str = "index.html",
) -> ToolResult:
    """Run ``check`` on one HTML file with a canned html-validate run.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        returncode: html-validate's exit status.
        stdout: The JSON report stream.
        stderr: The diagnostics stream.
        file: The one relative path passed to html-validate.

    Returns:
        The check result.
    """
    ctx = MagicMock(should_skip=False, early_result=None, timeout=30)
    ctx.cwd = str(tmp_path)
    ctx.files = ctx.rel_files = [file]
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


def test_glob_named_file_with_no_files_matching_still_fails(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A literal ``[slug].html`` html-validate globbed away is not "no files"."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=1,
        stdout="",
        stderr="No files matching patterns [ '[slug].html' ]\n",
        file="[slug].html",
    )

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()


@pytest.mark.parametrize(
    ("stderr", "file_args", "expected"),
    [
        pytest.param(NO_FILES_MATCHING, ["index.html"], True, id="message-line"),
        pytest.param(
            f"(node) warning: something\n{NO_FILES_MATCHING}",
            ["index.html"],
            True,
            id="message-on-a-later-line",
        ),
        pytest.param(
            f"Error: crashed while reading: {NO_FILES_MATCHING}",
            ["index.html"],
            False,
            id="message-inside-another-line",
        ),
        pytest.param(NO_FILES_MATCHING, ["a{1,2}.html"], False, id="brace-path"),
        pytest.param(NO_FILES_MATCHING, ["dir\\file.html"], False, id="backslash"),
        pytest.param(NO_FILES_MATCHING, ["+(a).html"], False, id="extglob-plus"),
        pytest.param(NO_FILES_MATCHING, ["@(a).html"], False, id="extglob-at"),
        pytest.param(
            NO_FILES_MATCHING,
            ["!a.html", "#b.html", "c,d.html"],
            True,
            id="literal-negation-comment-comma",
        ),
        pytest.param(
            NO_FILES_MATCHING,
            ["index.html", "pages/[id].html"],
            False,
            id="any-glob-path",
        ),
    ],
)
def test_ignored_every_file_anchors_message_and_rejects_glob_paths(
    stderr: str,
    file_args: list[str],
    expected: bool,
) -> None:
    """Only a line starting with the message, with literal paths, is "no files".

    Args:
        stderr: html-validate's diagnostics.
        file_args: Paths passed to html-validate.
        expected: Whether the run reads as all-ignored.
    """
    assert_that(
        ignored_every_file(
            success=False,
            stdout="",
            stderr=stderr,
            file_args=file_args,
        ),
    ).is_equal_to(expected)
