"""html-validate runs whose files its own config ignores are "no files" (#3393).

When ``.htmlvalidateignore`` excludes every file it was given, html-validate
11.21 prints nothing to stdout, writes ``No files matching patterns [...]``
to stderr and exits 1.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate import definition
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


@pytest.mark.parametrize(
    ("sep", "held_back"),
    [pytest.param("/", True, id="posix"), pytest.param("\\", False, id="windows")],
)
def test_backslash_hold_back_applies_on_posix_only(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sep: str,
    held_back: bool,
) -> None:
    r"""On Windows ``dir\file.html`` names a subdirectory file and is validated.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Working directory for the context.
        monkeypatch: Pins the platform separator the plugin judges paths by.
        sep: The platform path separator.
        held_back: Whether the path is withheld from html-validate.
    """
    monkeypatch.setattr(
        definition,
        "matches_itself",
        partial(definition.matches_itself, sep=sep),
    )
    ctx = MagicMock(should_skip=False, early_result=None, timeout=30)
    ctx.cwd = str(tmp_path)
    ctx.files = ctx.rel_files = ["dir\\file.html"]
    clean = SubprocessResult(returncode=0, stdout="[]", stderr="", output="[]")
    with (
        patch.object(html_validate_plugin, "_prepare_execution", return_value=ctx),
        patch.object(
            html_validate_plugin,
            "_get_executable_command",
            return_value=["/pinned/html-validate"],
        ),
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=clean,
        ) as run,
    ):
        result = html_validate_plugin.check([str(tmp_path)], {})

    commands = [call.kwargs["cmd"] for call in run.call_args_list]
    assert_that(bool(commands)).is_equal_to(not held_back)
    assert_that(result.execution_failed).is_equal_to(held_back)
    assert_that(result.success).is_equal_to(not held_back)
    if not held_back:
        assert_that(commands[0]).contains("dir\\file.html")


@pytest.mark.parametrize(
    ("path", "sep", "expected"),
    [
        pytest.param("dir\\file.html", "/", False, id="posix-backslash"),
        pytest.param("dir\\file.html", "\\", True, id="windows-separator"),
        pytest.param("[slug].html", "\\", False, id="windows-brackets"),
        pytest.param("a{1,2}.html", "\\", False, id="windows-braces"),
        pytest.param("+(a).html", "\\", False, id="windows-extglob"),
        pytest.param("pages\\index.html", "\\", True, id="windows-plain"),
    ],
)
def test_matches_itself_by_platform(path: str, sep: str, expected: bool) -> None:
    """Glob characters are unsafe everywhere; a backslash only on POSIX.

    Args:
        path: A relative path.
        sep: The platform path separator.
        expected: Whether html-validate's expansion maps the path to itself.
    """
    assert_that(definition.matches_itself(path, sep=sep)).is_equal_to(expected)


# Each case was checked against html-validate 11.21.0: ``True`` names resolve
# to themselves, ``False`` names resolve to another file or to nothing.
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("(app)/page.html", True, id="sveltekit-route-group"),
        pytest.param("src/routes/(app)/+page.svelte", True, id="route-group-page"),
        pytest.param("page (1).html", True, id="copy-suffix"),
        pytest.param("a(b.html", True, id="lone-open-paren"),
        pytest.param("x!y(z).html", True, id="bang-not-before-paren"),
        pytest.param("a]b[c.html", True, id="bracket-without-later-close"),
        pytest.param("{a}b{c}.html", True, id="braces-without-comma"),
        pytest.param("a}b,c{.html", True, id="brace-without-later-close"),
        pytest.param("a+(b).html", False, id="extglob-plus"),
        pytest.param("x@(y).html", False, id="extglob-at"),
        pytest.param("!(z).html", False, id="extglob-bang"),
        pytest.param("?(q).html", False, id="extglob-question"),
        pytest.param("*(r).html", False, id="extglob-star"),
        pytest.param("[a].html", False, id="character-class"),
        pytest.param("[]].html", False, id="character-class-of-close"),
        pytest.param("(x)/[y]/p.html", False, id="class-in-directory"),
        pytest.param("{a,b}.html", False, id="brace-list"),
        pytest.param("{1..2}.html", False, id="brace-range"),
        pytest.param("{a}b,c}.html", False, id="comma-before-later-brace"),
        pytest.param("a*b.html", False, id="star"),
        pytest.param("a?b.html", False, id="question"),
        pytest.param("[].html", False, id="empty-class-held-back-anyway"),
        pytest.param("a+(b.html", False, id="unclosed-extglob-held-back-anyway"),
        pytest.param("[a\nb].html", False, id="newline-inside-class"),
        pytest.param("[a\nb]/p.html", False, id="newline-inside-class-directory"),
        pytest.param("[a\tb].html", False, id="tab-inside-class"),
        pytest.param("{a,\nb}.html", False, id="newline-inside-brace-list"),
        pytest.param("{a\n..b}.html", False, id="newline-inside-brace-range"),
        pytest.param("a+(\nb).html", False, id="newline-inside-extglob"),
        pytest.param("a\nb.html", False, id="bare-newline"),
        pytest.param("a\tb.html", False, id="bare-tab"),
        pytest.param("a\x01b.html", False, id="c0-control"),
        pytest.param("a\x7fb.html", False, id="delete"),
        pytest.param("a\x85b.html", False, id="c1-control"),
    ],
)
def test_matches_itself_holds_back_only_misread_shapes(
    path: str,
    expected: bool,
) -> None:
    """Parentheses, brackets and braces are unsafe only in glob shapes.

    Args:
        path: A relative path on POSIX.
        expected: Whether html-validate's expansion maps the path to itself.
    """
    assert_that(definition.matches_itself(path, sep="/")).is_equal_to(expected)
