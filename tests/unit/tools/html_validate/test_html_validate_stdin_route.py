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
from dataclasses import dataclass, field
from pathlib import Path
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
    CONFIG_NOT_ALLOWED,
    FLAT_CONFIG_PRESENT,
    IGNORE_NOT_INAPPLICABLE,
    SYMLINKED_PATH,
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
        f"'[slug].html': {IGNORE_NOT_INAPPLICABLE}: ",
        ".htmlvalidateignore",
        "not inspected",
    )
    assert_that(result.output).does_not_contain("transform", "cannot be read")


def _reason(tmp_path: Path, path: str, *, cwd: Path | None = None) -> str | None:
    """Ask a fresh route why a file is held back.

    Args:
        tmp_path: Default working directory.
        path: The file, relative to the working directory.
        cwd: The run's working directory, when not ``tmp_path``.

    Returns:
        The hold-back reason, or None.
    """
    return StdinRoute(cwd=str(cwd or tmp_path)).hold_back_reason(path)


def _write(path: Path, content: str = "<p>a</p>") -> Path:
    """Write a file, creating its directory.

    Args:
        path: The file.
        content: What to write.

    Returns:
        The path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_a_plain_tree_goes_to_stdin(tmp_path: Path) -> None:
    """No config, no ignore file and no symlink: stdin is faithful.

    Args:
        tmp_path: Root of the tree.
    """
    _write(tmp_path / "[slug]" / "page.html")

    assert_that(_reason(tmp_path, "[slug]/page.html")).is_none()
    assert_that(_reason(tmp_path, "missing[1].html")).is_equal_to(
        "the file cannot be read",
    )


@pytest.mark.parametrize(
    "name",
    [
        "html-validate.config.mjs",
        "html-validate.config.cjs",
        "html-validate.config.js",
        "html-validate.config.mts",
        "html-validate.config.cts",
        "html-validate.config.ts",
    ],
)
def test_a_flat_config_above_the_file_holds_it_back(tmp_path: Path, name: str) -> None:
    """The CLI prefers a flat config, whose blocks match ``/dev/stdin``.

    Args:
        tmp_path: Root of the tree.
        name: The flat config file name.
    """
    flat = _write(tmp_path / name, "export default [];\n")
    _write(tmp_path / "[slug]" / "page.html")

    assert_that(_reason(tmp_path, "[slug]/page.html")).is_equal_to(
        f"{FLAT_CONFIG_PRESENT}: {flat}",
    )


def test_a_flat_config_above_the_working_directory_holds_back(tmp_path: Path) -> None:
    """The batch call looks up from the working directory, so it counts too.

    Args:
        tmp_path: Root of the tree.
    """
    flat = _write(tmp_path / "run" / "html-validate.config.mjs", "export default [];\n")
    target = _write(tmp_path / "pages" / "[slug].html")

    assert_that(_reason(tmp_path, str(target), cwd=tmp_path / "run")).is_equal_to(
        f"{FLAT_CONFIG_PRESENT}: {flat}",
    )


@pytest.mark.parametrize(
    "linked",
    [
        pytest.param("directory", id="symlinked-directory"),
        pytest.param("file", id="symlinked-file"),
        pytest.param("cwd", id="symlinked-working-directory"),
    ],
)
def test_a_symlink_anywhere_holds_the_file_back(tmp_path: Path, linked: str) -> None:
    """A symlink can make the stdin working directory resolve elsewhere.

    Args:
        tmp_path: Root of the tree.
        linked: Which part of the path is a symlink.
    """
    physical = _write(tmp_path / "physical" / "[slug].html").parent
    project = tmp_path / "project"
    project.mkdir()
    cwd = project
    if linked == "directory":
        (project / "linked").symlink_to(physical, target_is_directory=True)
        path = "linked/[slug].html"
    elif linked == "file":
        (project / "[slug].html").symlink_to(physical / "[slug].html")
        path = "[slug].html"
    else:
        (tmp_path / "alias").symlink_to(physical, target_is_directory=True)
        cwd = tmp_path / "alias"
        path = "[slug].html"

    reason = _reason(tmp_path, path, cwd=cwd)

    assert_that(reason).starts_with(f"{SYMLINKED_PATH}: ")


_JSON = ".htmlvalidate.json"
_NOT_BUILTIN = "extends something other than a built-in preset"
_SCRIPT = "module.exports = {};"


@pytest.mark.parametrize(
    ("name", "content", "detail"),
    [
        pytest.param(_JSON, '{"transform": {}}', "has key 'transform'", id="transform"),
        pytest.param(_JSON, '{"plugins": []}', "has key 'plugins'", id="plugins"),
        pytest.param(
            _JSON,
            '{"elements": ["html5"]}',
            "has key 'elements'",
            id="elements",
        ),
        pytest.param(_JSON, '{"extends": [{}]}', _NOT_BUILTIN, id="extends-non-string"),
        pytest.param(
            _JSON,
            '{"extends": ["html-validate:custom.cjs"]}',
            _NOT_BUILTIN,
            id="html-validate-prefixed-file",
        ),
        pytest.param(
            _JSON,
            '{"extends": ["my-shared-config"]}',
            _NOT_BUILTIN,
            id="external-preset",
        ),
        pytest.param(
            _JSON,
            '{"extends": "html-validate:recommended"}',
            _NOT_BUILTIN,
            id="extends-not-a-list",
        ),
        pytest.param(_JSON, "{not json", "cannot be read", id="bad-json"),
        pytest.param(_JSON, "[]", "is not an object", id="not-object"),
        pytest.param(".htmlvalidate.js", _SCRIPT, "is not JSON", id="js"),
        pytest.param(".htmlvalidate.cjs", _SCRIPT, "is not JSON", id="cjs"),
        pytest.param(".htmlvalidate.mjs", _SCRIPT, "is not JSON", id="mjs"),
    ],
)
def test_a_config_off_the_allowlist_holds_the_file_back(
    tmp_path: Path,
    name: str,
    content: str,
    detail: str,
) -> None:
    """Only JSON configs with allowlisted keys and built-in presets pass.

    Args:
        tmp_path: Root of the tree.
        name: The config file name, one directory above the file.
        content: Its content.
        detail: What the reason must say about the config.
    """
    config = _write(tmp_path / name, content)
    _write(tmp_path / "[slug]" / "page.html")

    assert_that(_reason(tmp_path, "[slug]/page.html")).is_equal_to(
        f"{CONFIG_NOT_ALLOWED}: {config} {detail}",
    )


def test_an_allowlisted_config_goes_to_stdin(tmp_path: Path) -> None:
    """Every allowed key and every built-in preset together still pass.

    Args:
        tmp_path: Root of the tree.
    """
    _write(
        tmp_path / ".htmlvalidate.json",
        json.dumps(
            {
                "$schema": "https://html-validate.org/schemas/config.json",
                "root": True,
                "extends": [
                    "html-validate:a11y",
                    "html-validate:browser",
                    "html-validate:document",
                    "html-validate:prettier",
                    "html-validate:recommended",
                    "html-validate:standard",
                ],
                "rules": {"close-order": "off"},
            },
        ),
    )
    _write(tmp_path / "[slug]" / ".htmlvalidate.json", '{"rules": {}}')
    _write(tmp_path / "[slug]" / "page.html")

    assert_that(_reason(tmp_path, "[slug]/page.html")).is_none()


@pytest.mark.parametrize(
    ("ignore", "name", "held"),
    [
        pytest.param("dist/\n# [slug].html\n\n", "[slug].html", False, id="elsewhere"),
        pytest.param("src/?slug?.html\n", "[slug].html", True, id="relative-to-root"),
        pytest.param("src/?SLUG?.HTML\n", "[slug].html", True, id="other-case"),
        pytest.param("*.HTML\n", "[slug].html", True, id="other-case-extension"),
        pytest.param("*.html\n!src/?slug?.html\n", "[slug].html", True, id="negation"),
        pytest.param("src/\n!src/?slug?.html\n", "[slug].html", True, id="parent"),
        pytest.param("[z-a].html\n", "[slug].html", True, id="invalid-range"),
        pytest.param("????.html\n", "[\U0001f600].html", True, id="non-ascii-path"),
        pytest.param("?Σ?.html\n", "[slug].html", True, id="non-ascii-pattern"),
    ],
)
def test_an_ignore_file_must_be_provably_inapplicable(
    tmp_path: Path,
    ignore: str,
    name: str,
    held: bool,
) -> None:
    """Only ASCII ignore files whose patterns cannot match let a file through.

    Matching is case-insensitive with negations dropped, so a negation can
    only add hold-backs. Non-ASCII content is never trusted: JavaScript
    counts UTF-16 units and folds case differently.

    Args:
        tmp_path: Root of the tree.
        ignore: The root ``.htmlvalidateignore`` content.
        name: The file name under ``src/``.
        held: Whether the file is held back.
    """
    ignore_file = _write(tmp_path / ".htmlvalidateignore", ignore)
    _write(tmp_path / "src" / name)

    reason = _reason(tmp_path, f"src/{name}")

    expected = f"{IGNORE_NOT_INAPPLICABLE}: {ignore_file}" if held else None
    assert_that(reason).is_equal_to(expected)


def test_an_unreadable_ignore_file_holds_the_file_back(tmp_path: Path) -> None:
    """An ignore file lintro cannot read could ignore anything.

    Args:
        tmp_path: Root of the tree.
    """
    _write(tmp_path / "[slug].html")
    (tmp_path / ".htmlvalidateignore").mkdir()

    assert_that(_reason(tmp_path, "[slug].html")).is_equal_to(
        f"{IGNORE_NOT_INAPPLICABLE}: {tmp_path / '.htmlvalidateignore'}",
    )


def test_paths_resolve_dot_dot_segments_like_node(tmp_path: Path) -> None:
    """``a/../[s].html`` names the same file html-validate would read.

    Args:
        tmp_path: Root of the tree.
    """
    route = StdinRoute(cwd=str(tmp_path))

    assert_that(route.absolute("a/../[s].html")).is_equal_to(tmp_path / "[s].html")


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
