"""Unit tests for the html-validate plugin execution methods."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.html_validate import (
    HtmlValidatePlugin,
    contains_glob_syntax,
)

_ISSUE_JSON = (
    '[{"filePath":"index.html","messages":['
    '{"ruleId":"wcag/h37","severity":2,'
    '"message":"<img> is missing required \\"alt\\" attribute",'
    '"line":5,"column":2,"selector":"html > body > img",'
    '"ruleUrl":"https://html-validate.org/rules/wcag/h37.html"}],'
    '"errorCount":1,"warningCount":0}]'
)


def _mock_ctx(tmp_path: Path, files: list[str]) -> MagicMock:
    """Build a mock ExecutionContext.

    Args:
        tmp_path: Working directory for the mock context.
        files: File list to expose on the context.

    Returns:
        A configured MagicMock standing in for an ExecutionContext.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.files = files
    ctx.rel_files = files
    return ctx


def test_check_with_issues(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Check returns structured issues parsed from JSON stdout.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture file.
    """
    html_file = tmp_path / "index.html"
    html_file.write_text("<img src='a.png'>\n")

    mock_result = SubprocessResult(
        returncode=1,
        stdout=_ISSUE_JSON,
        stderr="",
        output=_ISSUE_JSON,
    )

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path, [str(html_file)])
        result = html_validate_plugin.check([str(html_file)], {})

    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issue = cast(HtmlValidateIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("wcag/h37")
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.line).is_equal_to(5)


def test_check_clean_suppresses_output(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A clean run reports success and suppresses output.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture file.
    """
    html_file = tmp_path / "clean.html"
    html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(
        returncode=0,
        stdout="[]",
        stderr="",
        output="[]",
    )

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path, [str(html_file)])
        result = html_validate_plugin.check([str(html_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_returns_early_when_skipped(
    html_validate_plugin: HtmlValidatePlugin,
) -> None:
    """Check returns the early result when preparation signals a skip.

    Args:
        html_validate_plugin: The plugin under test.
    """
    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early

    with patch.object(
        html_validate_plugin,
        "_prepare_execution",
        return_value=ctx,
    ):
        result = html_validate_plugin.check(["x.html"], {})

    assert_that(result).is_same_as(early)


def test_check_passes_literal_paths_and_pinned_executable(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Only literal discovered paths are passed, with a pinned executable.

    html-validate expands glob patterns through ``fs.globSync``, which aborts
    the run on Bun builds lacking it, and an unpinned ``@latest`` spec resolves
    a fresh release on every run (issue #1727).

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture files.
    """
    first = tmp_path / "index.html"
    second = tmp_path / "about.html"
    for html_file in (first, second):
        html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(returncode=0, stdout="[]", stderr="", output="[]")
    rel_files = ["index.html", "about.html"]

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ) as mock_run,
    ):
        ctx = _mock_ctx(tmp_path, rel_files)
        mock_prepare.return_value = ctx
        html_validate_plugin.check([str(tmp_path)], {})

    cmd = cast(list[str], mock_run.call_args.kwargs["cmd"])
    assert_that(cmd[-2:]).is_equal_to(rel_files)
    for argument in cmd:
        assert_that(contains_glob_syntax(argument)).described_as(argument).is_false()
    assert_that(" ".join(cmd)).does_not_contain("@latest")


def test_contains_glob_syntax_detects_metacharacters() -> None:
    """Glob metacharacters are detected; plain paths are not flagged."""
    assert_that(contains_glob_syntax("src/**/*.html")).is_true()
    assert_that(contains_glob_syntax("page[1].html")).is_true()
    assert_that(contains_glob_syntax("src/index.html")).is_false()


def test_check_falls_back_to_absolute_files(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Absolute discovered files are used when no relative paths are available.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture file.
    """
    html_file = tmp_path / "index.html"
    html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(returncode=0, stdout="[]", stderr="", output="[]")

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ) as mock_run,
    ):
        ctx = _mock_ctx(tmp_path, [str(html_file)])
        ctx.rel_files = []
        mock_prepare.return_value = ctx
        html_validate_plugin.check([str(html_file)], {})

    cmd = cast(list[str], mock_run.call_args.kwargs["cmd"])
    assert_that(cmd[-1]).is_equal_to(str(html_file))


def test_fix_raises_not_implemented(
    html_validate_plugin: HtmlValidatePlugin,
) -> None:
    """html-validate is check-only; fix() must raise NotImplementedError.

    Args:
        html_validate_plugin: The plugin under test.
    """
    with pytest.raises(NotImplementedError):
        html_validate_plugin.fix(["x.html"], {})


def test_check_prefers_the_target_projects_local_binary(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """The executable resolves from ctx.cwd, not the process working directory.

    When lintro checks a project outside its own working directory (``--diff``
    against another checkout, a package inside a monorepo), the run must use
    that project's lockfile-pinned ``node_modules/.bin/html-validate``.
    Resolving from the process cwd instead silently falls back to PATH or a
    registry fetch, so diagnostics come from a different installation than the
    one the project pins (#1727).

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory standing in for the target project.
    """
    project = tmp_path / "target-project"
    local_bin = project / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / "html-validate"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    html_file = project / "index.html"
    html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(returncode=0, stdout="[]", stderr="", output="[]")

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ) as mock_run,
    ):
        mock_prepare.return_value = _mock_ctx(project, [str(html_file)])
        html_validate_plugin.check([str(html_file)], {})

    cmd = cast(list[str], mock_run.call_args.kwargs["cmd"])
    # The project's own binary, not "html-validate" from PATH or a bunx spec.
    assert_that(cmd[0]).is_equal_to(binary.as_posix())


def test_check_does_not_fall_back_to_lintros_own_node_modules(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target without its own install must not use lintro's node_modules.

    Falling back to a process-cwd search would select an unrelated
    ``node_modules/.bin/html-validate`` ahead of PATH, making resolution depend
    on where lintro happens to be invoked from (#1727).

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory root.
        monkeypatch: Fixture used to relocate the process working directory.
    """
    # lintro's own tree has an install; the target project does not.
    lintro_tree = tmp_path / "lintro-cwd"
    stray_bin = lintro_tree / "node_modules" / ".bin"
    stray_bin.mkdir(parents=True)
    stray = stray_bin / "html-validate"
    stray.write_text("#!/bin/sh\n")
    stray.chmod(0o755)
    monkeypatch.chdir(lintro_tree)

    project = tmp_path / "target-project"
    project.mkdir()
    html_file = project / "index.html"
    html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(returncode=0, stdout="[]", stderr="", output="[]")

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ) as mock_run,
    ):
        mock_prepare.return_value = _mock_ctx(project, [str(html_file)])
        html_validate_plugin.check([str(html_file)], {})

    cmd = cast(list[str], mock_run.call_args.kwargs["cmd"])
    assert_that(cmd[0]).is_not_equal_to(stray.as_posix())
