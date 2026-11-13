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

from lintro.models.core.tool_outcome import Checked, NoFiles, TimedOut, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins.execution_preparation import PreparedExecution
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate.definition import HtmlValidatePlugin
from lintro.tools.html_validate.stdin_route import (
    TRANSFORM_CONFIGURED,
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
        slow: File names whose stdin call hits its deadline.
        vanished: File names whose stdin call finds no executable.
    """

    dirty: set[str] = field(default_factory=set)
    calls: list[_Call] = field(default_factory=list)
    slow: set[str] = field(default_factory=set)
    vanished: set[str] = field(default_factory=set)

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
            subprocess.TimeoutExpired: For a stdin call on a ``slow`` file.
            FileNotFoundError: For a stdin call on a ``vanished`` file.
        """
        del timeout
        source = None
        if isinstance(stdin, int) and stdin >= 0:
            source = os.read(stdin, 65536).decode()
        self.calls.append(_Call(cmd=list(cmd), cwd=cwd, source=source))
        if "--stdin" in cmd:
            name = cmd[cmd.index("--stdin-filename") + 1]
            if Path(name).name in self.slow:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
            if Path(name).name in self.vanished:
                raise FileNotFoundError(cmd[0])
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


def test_a_stdin_timeout_stops_the_run_and_keeps_counted_findings(
    tmp_path: Path,
) -> None:
    """After one stdin deadline no further call starts; findings stay counted.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate(dirty={"page.html"}, slow={"[a].html"})
    result = _check(
        tmp_path,
        {
            "page.html": "<p><div></p>",
            "[a].html": "<p>a</p>",
            "[b].html": "<p><div></p>",
            "[c].html": "<p><div></p>",
        },
        fake,
    )

    started = [call.cmd[-1] for call in fake.calls if "--stdin" in call.cmd]
    assert_that(started).is_equal_to([str(tmp_path / "[a].html")])
    assert_that(result.timed_out).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.success).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues_count).is_equal_to(len(result.issues)).is_equal_to(1)
    assert_that(result.output).contains(
        f"{tmp_path / '[a].html'}: ",
        "timed out",
        "not run on 2 file(s) because an earlier html-validate call timed out: "
        "'[b].html', '[c].html'.",
    )


def test_a_launch_failure_keeps_earlier_findings_and_names_the_file(
    tmp_path: Path,
) -> None:
    """An executable gone mid-run makes an incomplete run, not a lost one.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate(dirty={"page.html"}, vanished={"[b].html"})
    result = _check(
        tmp_path,
        {
            "page.html": "<p><div></p>",
            "[a].html": "<p><div></p>",
            "[b].html": "<p><div></p>",
        },
        fake,
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that({str(issue.file) for issue in result.issues}).is_equal_to(
        {"page.html", str(tmp_path / "[a].html")},
    )
    assert_that(result.output).starts_with(
        f"{tmp_path / '[b].html'}: html-validate could not be started",
    )


def test_a_dash_name_is_passed_as_a_path(tmp_path: Path) -> None:
    """``-x.html`` would be read as an option, so it is passed as ``./-x.html``.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _FakeHtmlValidate(dirty={"./-x.html"})
    result = _check(tmp_path, {"-x.html": "<p><div></p>", "ok.html": "<p></p>"}, fake)

    assert_that([call.cmd[3:] for call in fake.calls]).is_equal_to(
        [["./-x.html", "ok.html"]],
    )
    assert_that([str(issue.file) for issue in result.issues]).is_equal_to(
        ["./-x.html"],
    )


def test_a_file_under_an_ignore_file_is_held_back(tmp_path: Path) -> None:
    """Stdin bypasses ``.htmlvalidateignore``, so the hold-back is the fallback.

    Args:
        tmp_path: Working directory for the run.
    """
    (tmp_path / ".htmlvalidateignore").write_text("?slug?.html\n", encoding="utf-8")
    fake = _FakeHtmlValidate()
    result = _check(tmp_path, {"[slug].html": "<p><div></p>"}, fake)

    assert_that(fake.calls).is_empty()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains(
        "'[slug].html': a pattern in",
        ".htmlvalidateignore may ignore it",
        "not inspected",
    )
    assert_that(result.output).does_not_contain("transform", "cannot be read")


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
        pytest.param(_printed('{"rules": {}}'), None, id="no-transform"),
        pytest.param(
            _printed('{"transform": {}, "plugins": []}'),
            None,
            id="every-json-config-prints-an-empty-transform",
        ),
        pytest.param(
            _printed('{"transform": {}, "plugins": ["my-plugin"]}'),
            TRANSFORM_CONFIGURED,
            id="resolved-plugin",
        ),
        pytest.param(
            _printed('{"transform": {"^.*\\\\.vue$": "html-validate-vue"}}'),
            TRANSFORM_CONFIGURED,
            id="vue-only-transform",
        ),
        pytest.param(
            _printed('{"transform": {"\\\\bfoo\\\\.html$": "my-transform"}}'),
            TRANSFORM_CONFIGURED,
            id="js-word-boundary-pattern",
        ),
        pytest.param(
            _printed('{"transform": "not-an-object"}'),
            TRANSFORM_CONFIGURED,
            id="unreadable-transform-value",
        ),
        pytest.param(
            _printed("", returncode=1),
            "html-validate could not print the configuration for it",
            id="config-error",
        ),
        pytest.param(
            _printed("not json"),
            "html-validate could not print the configuration for it",
            id="unreadable-output",
        ),
        pytest.param(
            _printed("[]"),
            "html-validate could not print the configuration for it",
            id="not-an-object",
        ),
    ],
)
def test_any_configured_transform_holds_the_file_back(
    tmp_path: Path,
    printed: SubprocessResult,
    expected: str | None,
) -> None:
    """Any ``transform`` in the resolved config holds every file back.

    Transform keys are JavaScript patterns (a word boundary differs from
    Python's around ``é``), and function values print as ``{}``, so lintro
    never tries to match them.

    Args:
        tmp_path: Directory holding the config and the file.
        printed: What ``--print-config --stdin`` answered.
        expected: The hold-back reason, or None when stdin is used.
    """
    (tmp_path / ".htmlvalidate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "[slug]").mkdir()
    for name in ("page.html", "other.html"):
        (tmp_path / "[slug]" / name).write_text("<p>a</p>", encoding="utf-8")
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

    assert_that(route.hold_back_reason("[slug]/page.html")).is_equal_to(expected)
    assert_that(route.hold_back_reason("[slug]/other.html")).is_equal_to(expected)
    assert_that(asked).is_equal_to(
        [
            (
                [_EXECUTABLE, "--formatter", "json", "--print-config", "--stdin"],
                str(tmp_path / "[slug]"),
            ),
        ],
    )


def _no_config_call(cmd: list[str], cwd: str) -> NoReturn:
    """Fail the test if html-validate is asked for its config.

    Args:
        cmd: The argv.
        cwd: The working directory.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError(f"unexpected call {cmd} in {cwd}")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(
            '{"transform": {}}',
            f"{TRANSFORM_CONFIGURED} in {{config}}",
            id="transform-key-even-if-empty",
        ),
        pytest.param(
            '{"plugins": ["html-validate-vue"]}',
            "a transform may be configured: {config} loads a plugin",
            id="plugin",
        ),
        pytest.param(
            '{"extends": ["html-validate:recommended", "my-shared-config"]}',
            "a transform may be configured: {config} loads a plugin",
            id="external-preset",
        ),
        pytest.param(
            '{"extends": "html-validate:recommended"}',
            "a transform may be configured: {config} loads a plugin",
            id="extends-not-a-list",
        ),
        pytest.param("{not json", "{config} cannot be read", id="invalid-json"),
        pytest.param("[]", "{config} cannot be read", id="not-an-object"),
    ],
)
def test_a_json_config_that_may_transform_holds_back_without_asking(
    tmp_path: Path,
    config: str,
    expected: str,
) -> None:
    """Function transforms print as ``{}``, so the config files decide first.

    Args:
        tmp_path: Root of the tree.
        config: The ``.htmlvalidate.json`` content, one directory up.
        expected: Start of the hold-back reason.
    """
    config_file = tmp_path / ".htmlvalidate.json"
    config_file.write_text(config, encoding="utf-8")
    (tmp_path / "[slug]").mkdir()
    (tmp_path / "[slug]" / "page.html").write_text("<p>a</p>", encoding="utf-8")

    reason = _route(tmp_path, _no_config_call).hold_back_reason("[slug]/page.html")

    assert_that(reason).starts_with(expected.format(config=config_file))


def test_without_a_config_file_stdin_is_used_without_asking(tmp_path: Path) -> None:
    """The built-in preset has no transformers, so nothing extra runs.

    Args:
        tmp_path: Directory holding the file.
    """
    (tmp_path / "[slug].html").write_text("<p>a</p>", encoding="utf-8")
    route = _route(tmp_path, _no_config_call)

    assert_that(route.hold_back_reason("[slug].html")).is_none()
    assert_that(route.hold_back_reason("missing[1].html")).is_equal_to(
        "the file cannot be read",
    )


_MAY_IGNORE = "a pattern in {root}/.htmlvalidateignore may ignore it"


@pytest.mark.parametrize(
    ("ignores", "expected"),
    [
        pytest.param({"": "dist/\n"}, None, id="pattern-for-another-directory"),
        pytest.param(
            {"": "# src/?slug?.html\n\n"},
            None,
            id="comment-and-blank-lines",
        ),
        pytest.param(
            {"": "src/?slug?.html\n"},
            _MAY_IGNORE,
            id="root-pattern-relative-to-its-directory",
        ),
        pytest.param(
            {"src": "?slug?.html\n"},
            "a pattern in {root}/src/.htmlvalidateignore may ignore it",
            id="nearer-ignore-file",
        ),
        pytest.param({"": "src/\n"}, _MAY_IGNORE, id="directory"),
        pytest.param({"": "src/?SLUG?.HTML\n"}, _MAY_IGNORE, id="upper-case-name"),
        pytest.param({"": "*.HTML\n"}, _MAY_IGNORE, id="upper-case-extension"),
        pytest.param(
            {"": "*.html\n!src/?slug?.html\n"},
            _MAY_IGNORE,
            id="negation-never-re-includes",
        ),
        pytest.param(
            {"": "src/\n!src/?slug?.html\n"},
            _MAY_IGNORE,
            id="excluded-parent-stays-excluded",
        ),
        pytest.param(
            {"": "[z-a].html\n"},
            "{root}/.htmlvalidateignore cannot be read or parsed",
            id="invalid-range",
        ),
    ],
)
def test_ignore_files_hold_back_whatever_html_validate_might_ignore(
    tmp_path: Path,
    ignores: dict[str, str],
    expected: str | None,
) -> None:
    """Ignore patterns match case-insensitively and negations are dropped.

    html-validate's ``ignore`` package is case-insensitive and keeps a file
    under an excluded parent excluded. Dropping negations means the check
    can only hold back more than html-validate ignores, never less.

    Args:
        tmp_path: Root of the tree.
        ignores: Directory (relative to the root) to ignore-file content.
        expected: Substring of the hold-back reason, or None.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "[slug].html").write_text("<p>a</p>", encoding="utf-8")
    for folder, content in ignores.items():
        (tmp_path / folder / ".htmlvalidateignore").write_text(
            content,
            encoding="utf-8",
        )

    reason = _route(tmp_path, _no_config_call).hold_back_reason("src/[slug].html")

    if expected is None:
        assert_that(reason).is_none()
    else:
        assert_that(reason).contains(expected.format(root=tmp_path))


def test_an_unreadable_ignore_file_holds_the_file_back(tmp_path: Path) -> None:
    """An ignore file lintro cannot read could ignore anything.

    Args:
        tmp_path: Root of the tree.
    """
    (tmp_path / "[slug].html").write_text("<p>a</p>", encoding="utf-8")
    (tmp_path / ".htmlvalidateignore").mkdir()

    reason = _route(tmp_path, _no_config_call).hold_back_reason("[slug].html")

    assert_that(reason).is_equal_to(
        f"{tmp_path / '.htmlvalidateignore'} cannot be read or parsed",
    )


@pytest.mark.parametrize(
    "name",
    [".htmlvalidate.js", ".htmlvalidate.cjs", ".htmlvalidate.mjs"],
)
def test_a_javascript_config_holds_the_file_back_without_asking(
    tmp_path: Path,
    name: str,
) -> None:
    """A JavaScript config can build transforms lintro cannot see.

    Args:
        tmp_path: Root of the tree.
        name: The JavaScript config file, one directory above the file.
    """
    (tmp_path / name).write_text("module.exports = {};\n", encoding="utf-8")
    (tmp_path / "[slug]").mkdir()
    (tmp_path / "[slug]" / "page.html").write_text("<p>a</p>", encoding="utf-8")

    reason = _route(tmp_path, _no_config_call).hold_back_reason("[slug]/page.html")

    assert_that(reason).starts_with("a transform may be configured: ").contains(
        str(tmp_path / name),
    )


def test_paths_resolve_dot_dot_segments_like_node(tmp_path: Path) -> None:
    """``a/../[s].html`` names the same file html-validate would read.

    Args:
        tmp_path: Root of the tree.
    """
    route = _route(tmp_path, _no_config_call)

    assert_that(route.absolute("a/../[s].html")).is_equal_to(tmp_path / "[s].html")


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

    assert_that(
        _route(tmp_path, answer).hold_back_reason("[slug].html"),
    ).is_equal_to("html-validate could not print the configuration for it")


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
    )

    assert_that(merged.execution_failed).is_true()
    assert_that(merged.inspected_files).is_none()
    assert_that(merged.output).contains("printed no report")
    assert_that([issue.file for issue in merged.issues]).is_equal_to(["a.html"])


def test_merge_keeps_counts_and_every_reason_beside_a_timeout() -> None:
    """A timeout part does not reset the counts or replace a crash reason."""
    timed_out = ToolResult.from_outcome(
        ToolOutcome(execution=TimedOut("/r/[t].html: timed out")),
        name="html_validate",
    )
    crashed = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=False, inspected_files=None)),
        name="html_validate",
        output="/r/[c].html: html-validate could not be started",
        execution_failed=True,
    )

    merged = merge_results(
        [_checked("a.html", "b.html", success=False), crashed, timed_out],
        name="html_validate",
        cwd="/r",
    )

    assert_that(merged.timed_out).is_false()
    assert_that(merged.execution_failed).is_true()
    assert_that(merged.findings_complete).is_false()
    assert_that(merged.inspected_files).is_none()
    assert_that(merged.issues_count).is_equal_to(len(merged.issues)).is_equal_to(2)
    assert_that(merged.output).contains(
        "/r/[c].html: html-validate could not be started",
        "/r/[t].html: timed out",
    )
