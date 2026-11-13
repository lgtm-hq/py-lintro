"""Glob-named files are validated through html-validate's stdin (#3396).

html-validate expands positional paths as globs, so ``[slug].html`` never
matches itself. Such files are validated one per call with ``--stdin
--stdin-filename <path>``, run from the file's directory, and merged with
the positional call into one result.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - TimeoutExpired for a simulated deadline
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import Checked, NoFiles, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins.execution_preparation import PreparedExecution
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate.definition import HtmlValidatePlugin
from lintro.tools.html_validate.stdin_route import (
    StdinRoute,
    merge_results,
    stdin_command,
)

_EXECUTABLE = "/pinned/html-validate"


def _report(path: str, *, findings: bool) -> str:
    """Build html-validate's JSON report for one file.

    Args:
        path: The file path the report names.
        findings: Whether the file carries one error.

    Returns:
        The JSON report.
    """
    messages = (
        [
            {
                "ruleId": "close-order",
                "severity": 2,
                "message": "Unclosed element '<div>'",
                "line": 1,
                "column": 5,
            },
        ]
        if findings
        else []
    )
    return json.dumps([{"filePath": path, "messages": messages}])


@dataclass
class _Call:
    """One recorded html-validate invocation.

    Attributes:
        cmd: The argv.
        cwd: The working directory.
        source: What the call read on stdin, or None for no stdin.
    """

    cmd: list[str]
    cwd: str | None
    source: str | None


@dataclass
class _FakeHtmlValidate:
    """Answer html-validate calls the way html-validate 11.21 does.

    A positional call reports each argument; a stdin call reports the
    ``--stdin-filename`` and finds an error when the source holds ``<div>``.

    Attributes:
        dirty: Positional paths that carry an error.
        calls: Every recorded invocation.
        stdin_times_out: Whether stdin calls hit their deadline.
    """

    dirty: set[str] = field(default_factory=set)
    calls: list[_Call] = field(default_factory=list)
    stdin_times_out: bool = False

    def __call__(
        self,
        *,
        cmd: list[str],
        timeout: float | None = None,
        cwd: str | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        """Record one call and answer it.

        Args:
            cmd: The argv.
            timeout: The deadline (unused).
            cwd: The working directory.
            stdin: A readable file descriptor, or None.

        Returns:
            The canned html-validate result.

        Raises:
            subprocess.TimeoutExpired: For a stdin call, when it times out.
        """
        del timeout
        source = None
        if isinstance(stdin, int) and stdin >= 0:
            source = os.read(stdin, 65536).decode()
        self.calls.append(_Call(cmd=list(cmd), cwd=cwd, source=source))
        if "--stdin" in cmd:
            if self.stdin_times_out:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
            name = cmd[cmd.index("--stdin-filename") + 1]
            dirty = "<div>" in (source or "")
            stdout = _report(name, findings=dirty)
        else:
            paths = cmd[cmd.index("json") + 1 :]
            stdout = json.dumps(
                [
                    json.loads(_report(path, findings=path in self.dirty))[0]
                    for path in paths
                ],
            )
            dirty = any(path in self.dirty for path in paths)
        code = 1 if dirty else 0
        return SubprocessResult(
            returncode=code,
            stdout=stdout,
            stderr="",
            output=stdout,
        )


def _check(
    tmp_path: Path,
    files: dict[str, str],
    fake: _FakeHtmlValidate,
) -> ToolResult:
    """Write files and run ``check`` over them with a fake html-validate.

    Args:
        tmp_path: Working directory for the run.
        files: Relative path to content.
        fake: The html-validate stand-in.

    Returns:
        The check result.
    """
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    ctx = PreparedExecution(
        files=[str(tmp_path / name) for name in files],
        rel_files=list(files),
        cwd=str(tmp_path),
        timeout=30,
    )
    plugin = HtmlValidatePlugin()
    with (
        patch.object(plugin, "prepare", return_value=ctx),
        patch.object(plugin, "_get_executable_command", return_value=[_EXECUTABLE]),
        patch.object(plugin, "_run_subprocess_result", side_effect=fake),
    ):
        return plugin.check([str(tmp_path)], {})


def test_stdin_command_passes_no_positional_path() -> None:
    """The stdin argv names the file only as the reported filename."""
    cmd = stdin_command([_EXECUTABLE, "--formatter", "json"], filename="/r/[a].html")

    assert_that(cmd).is_equal_to(
        [
            _EXECUTABLE,
            "--formatter",
            "json",
            "--stdin",
            "--stdin-filename",
            "/r/[a].html",
        ],
    )


def test_glob_named_files_are_validated_through_stdin(tmp_path: Path) -> None:
    """Only self-matching paths are positional; each glob name goes via stdin.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate(dirty={"page.html"})
    result = _check(
        tmp_path,
        {
            "page.html": "<p><div></p>",
            "[slug]/page.html": "<p><div></p>",
            "a{1,2}.html": "<p>clean</p>",
        },
        fake,
    )

    positional = [call for call in fake.calls if "--stdin" not in call.cmd]
    piped = [call for call in fake.calls if "--stdin" in call.cmd]
    assert_that([call.cmd[3:] for call in positional]).is_equal_to([["page.html"]])
    assert_that({(call.cmd[-1], call.cwd, call.source) for call in piped}).is_equal_to(
        {
            (
                str(tmp_path / "[slug]" / "page.html"),
                str(tmp_path / "[slug]"),
                "<p><div></p>",
            ),
            (str(tmp_path / "a{1,2}.html"), str(tmp_path), "<p>clean</p>"),
        },
    )
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that({str(issue.file) for issue in result.issues}).is_equal_to(
        {"page.html", str(tmp_path / "[slug]" / "page.html")},
    )


def test_only_glob_named_clean_files_pass(tmp_path: Path) -> None:
    """With no plain file, no positional call runs and the run passes.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate()
    result = _check(tmp_path, {"[slug].html": "<p>a</p>", "x?.html": "<p>b</p>"}, fake)

    positional = [call.cmd for call in fake.calls if "--stdin" not in call.cmd]
    assert_that(positional).is_empty()
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).is_none()


def test_a_stdin_timeout_times_out_the_run_and_keeps_findings(
    tmp_path: Path,
) -> None:
    """A deadline on one stdin call is a timeout, with the other findings kept.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate(
        dirty={"page.html"},
        stdin_times_out=True,
    )
    result = _check(
        tmp_path,
        {"page.html": "<p><div></p>", "[slug].html": "<p>a</p>"},
        fake,
    )

    assert_that(result.timed_out).is_true()
    assert_that(result.success).is_false()
    assert_that([str(issue.file) for issue in result.issues]).is_equal_to(
        ["page.html"],
    )


def test_a_file_under_an_ignore_file_is_held_back(tmp_path: Path) -> None:
    """Stdin bypasses ``.htmlvalidateignore``, so the hold-back is the fallback.

    Args:
        tmp_path: Working directory for the run.
    """
    (tmp_path / ".htmlvalidateignore").write_text("other.html\n", encoding="utf-8")
    fake = _FakeHtmlValidate()
    result = _check(tmp_path, {"[slug].html": "<p><div></p>"}, fake)

    assert_that(fake.calls).is_empty()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains("[slug].html", "not inspected")


def _route(
    tmp_path: Path,
    answer: Callable[[list[str], str], SubprocessResult],
) -> StdinRoute:
    """Build a route rooted at ``tmp_path`` with a canned print-config.

    Args:
        tmp_path: Directory the relative paths resolve against.
        answer: Stands in for the html-validate call.

    Returns:
        The route.
    """
    return StdinRoute(
        base_command=[_EXECUTABLE, "--formatter", "json"],
        run_tool=answer,
        cwd=str(tmp_path),
    )


def _printed(config: str, *, returncode: int = 0) -> SubprocessResult:
    """Build a ``--print-config`` result.

    Args:
        config: What html-validate printed.
        returncode: Its exit status.

    Returns:
        The canned result.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=config,
        stderr="",
        output=config,
    )


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        pytest.param(_printed('{"rules": {}}'), True, id="no-transform"),
        pytest.param(_printed('{"transform": {}}'), True, id="empty-transform"),
        pytest.param(
            _printed('{"transform": {"^.*\\\\.vue$": "html-validate-vue"}}'),
            False,
            id="transform",
        ),
        pytest.param(_printed("", returncode=1), False, id="config-error"),
        pytest.param(_printed("not json"), False, id="unreadable-output"),
        pytest.param(_printed("[]"), False, id="not-an-object"),
    ],
)
def test_a_config_file_is_read_for_transformers(
    tmp_path: Path,
    printed: SubprocessResult,
    expected: bool,
) -> None:
    """With a config file present, only a transformer-free config uses stdin.

    Args:
        tmp_path: Directory holding the config and the file.
        printed: What ``--print-config --stdin`` answered.
        expected: Whether stdin may validate the file.
    """
    (tmp_path / ".htmlvalidate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "[slug]").mkdir()
    (tmp_path / "[slug]" / "page.html").write_text("<p>a</p>", encoding="utf-8")
    asked: list[tuple[list[str], str]] = []

    def answer(cmd: list[str], cwd: str) -> SubprocessResult:
        """Record the print-config call and answer it.

        Args:
            cmd: The argv.
            cwd: The working directory.

        Returns:
            The canned result.
        """
        asked.append((cmd, cwd))
        return printed

    route = _route(tmp_path, answer)

    assert_that(route.can_validate("[slug]/page.html")).is_equal_to(expected)
    assert_that(route.can_validate("[slug]/page.html")).is_equal_to(expected)
    assert_that(asked).is_equal_to(
        [
            (
                [_EXECUTABLE, "--formatter", "json", "--print-config", "--stdin"],
                str(tmp_path / "[slug]"),
            ),
        ],
    )


def test_without_a_config_file_stdin_is_used_without_asking(tmp_path: Path) -> None:
    """The built-in preset has no transformers, so nothing extra runs.

    Args:
        tmp_path: Directory holding the file.
    """
    (tmp_path / "[slug].html").write_text("<p>a</p>", encoding="utf-8")

    def answer(cmd: list[str], cwd: str) -> NoReturn:
        """Fail the test if html-validate is asked for its config.

        Args:
            cmd: The argv.
            cwd: The working directory.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(f"unexpected call {cmd} in {cwd}")

    route = _route(tmp_path, answer)

    assert_that(route.can_validate("[slug].html")).is_true()
    assert_that(route.can_validate("missing[1].html")).is_false()


def test_a_failed_print_config_launch_holds_the_file_back(tmp_path: Path) -> None:
    """An html-validate that cannot start answers nothing, so stdin is not used.

    Args:
        tmp_path: Directory holding the config and the file.
    """
    (tmp_path / ".htmlvalidate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "[slug].html").write_text("<p>a</p>", encoding="utf-8")

    def answer(cmd: list[str], cwd: str) -> NoReturn:
        """Fail to launch.

        Args:
            cmd: The argv.
            cwd: The working directory.

        Raises:
            FileNotFoundError: Always.
        """
        raise FileNotFoundError(cmd[0], cwd)

    assert_that(_route(tmp_path, answer).can_validate("[slug].html")).is_false()


def _issue(path: str) -> HtmlValidateIssue:
    """Build one html-validate finding.

    Args:
        path: The file it is reported under.

    Returns:
        The finding.
    """
    return HtmlValidateIssue(file=path, line=1, column=1, code="close-order")


def _checked(*paths: str, success: bool, failures: int | None = None) -> ToolResult:
    """Build a checked result carrying one finding per path.

    Args:
        *paths: Files with a finding each.
        success: The verdict.
        failures: Parse failures recorded, if any.

    Returns:
        The result.
    """
    issues = [_issue(path) for path in paths]
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=success, inspected_files=True),
            issues_count=len(issues),
        ),
        name="html_validate",
        output=None if success else f"findings in {paths}",
        issues=issues,
        parse_failures_count=failures,
    )


def _never_times_out(issues: list[BaseIssue]) -> NoReturn:
    """Fail the test if the merge builds a timeout.

    Args:
        issues: Findings it would carry.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError(f"unexpected timeout with {issues}")


def test_merge_keeps_every_finding_and_sums_parse_failures() -> None:
    """Merged findings, counts and outputs come from every part."""
    merged = merge_results(
        [
            _checked("a.html", success=False, failures=1),
            _checked(success=True),
            _checked("[b].html", success=False, failures=2),
        ],
        name="html_validate",
        cwd="/r",
        timed_out=_never_times_out,
    )

    assert_that(merged.success).is_false()
    assert_that(merged.execution_failed).is_false()
    assert_that(merged.inspected_files).is_true()
    assert_that([issue.file for issue in merged.issues]).is_equal_to(
        ["a.html", "[b].html"],
    )
    assert_that(merged.issues_count).is_equal_to(2)
    assert_that(merged.parse_failures_count).is_equal_to(3)
    assert_that(merged.output).contains("a.html", "[b].html")
    assert_that(merged.cwd).is_equal_to("/r")


def test_merge_drops_a_no_files_part() -> None:
    """Plain files html-validate's config ignored add nothing to the verdict."""
    ignored = ToolResult.from_outcome(
        ToolOutcome(execution=NoFiles()),
        name="html_validate",
        output="ignored",
    )
    stdin_part = _checked(success=True)

    merged = merge_results(
        [ignored, stdin_part],
        name="html_validate",
        cwd=None,
        timed_out=_never_times_out,
    )

    assert_that(merged.no_files).is_false()
    assert_that(merged.success).is_true()
    assert_that(merged.inspected_files).is_true()


def test_merge_of_a_failed_part_is_an_incomplete_run() -> None:
    """One part with no verdict makes the whole run incomplete."""
    broken = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=False, inspected_files=None)),
        name="html_validate",
        output="html-validate printed no report",
        execution_failed=True,
    )

    merged = merge_results(
        [_checked("a.html", success=False), broken],
        name="html_validate",
        cwd=None,
        timed_out=_never_times_out,
    )

    assert_that(merged.execution_failed).is_true()
    assert_that(merged.inspected_files).is_none()
    assert_that(merged.output).contains("printed no report")
    assert_that([issue.file for issue in merged.issues]).is_equal_to(["a.html"])
