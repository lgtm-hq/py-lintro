"""Glob-named files are validated in one Node process when possible (#3433).

The one-process script loads html-validate's API from the package the
resolved binary runs. These tests cover when that package is accepted, how a
script run is read against its per-file deadline, and how the plugin merges a
run with the stdin pool it falls back to. A Python stand-in plays ``node``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import PreparedExecution
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate import definition
from lintro.tools.html_validate.definition import HtmlValidatePlugin
from lintro.tools.html_validate.one_process import (
    FileVerdict,
    HtmlValidatePackage,
    OneProcessRun,
    OneProcessRuntime,
    locate_package,
    one_process_runtime,
    run_one_process,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="uses symlinks and an executable script as node",
)

_VERSION = "11.21.0"
_BIN = '#!/usr/bin/env node\n\nimport "../dist/esm/html-validate.js";\n'
_DIRTY = "<p><div></p>"
_SLOW = "<!-- slow -->"
_BOOM = "<!-- boom -->"
_CRASH = "<!-- crash -->"
_IGNORED = "<!-- ignored -->"

# The stand-in for ``node``: it ignores its arguments, reads the request the
# real script reads, and answers per file the way the real script does,
# steered by markers in each file's content.
_FAKE_NODE = """
import json, sys, time
request = json.loads(sys.stdin.read())
def write(line):
    sys.stdout.write(json.dumps(line) + "\\n")
    sys.stdout.flush()
write({"version": VERSION})
for index, filename in enumerate(request["files"]):
    with open(filename, encoding="utf-8") as handle:
        source = handle.read()
    if "<!-- crash -->" in source:
        sys.exit(1)
    if "<!-- slow -->" in source:
        time.sleep(30)
    if "<!-- boom -->" in source:
        write({"index": index, "error": "Error: boom"})
        continue
    if "<!-- ignored -->" in source:
        write({"index": index, "ignored": True})
        continue
    dirty = "<div>" in source
    messages = [{"ruleId": "close-order", "severity": 2,
                 "message": "Unclosed element '<div>'", "line": 1,
                 "column": 5}] if dirty else []
    report = json.dumps([{"filePath": filename, "messages": messages}]
                        if dirty else [])
    write({"index": index, "valid": not dirty, "report": report})
"""


def _package(root: Path, *, bin_text: str = _BIN, **manifest: object) -> Path:
    """Write an html-validate package and a ``node_modules/.bin`` link to it.

    Args:
        root: Directory that receives ``node_modules``.
        bin_text: Content of the package's bin entry.
        **manifest: ``package.json`` fields that replace the defaults.

    Returns:
        The ``node_modules/.bin/html-validate`` link.
    """
    package = root / "node_modules" / "html-validate"
    (package / "bin").mkdir(parents=True)
    (package / "dist" / "esm").mkdir(parents=True)
    (package / "dist" / "esm" / "index.js").write_text("export {};\n")
    (package / "bin" / "html-validate.mjs").write_text(bin_text)
    fields: dict[str, object] = {
        "name": "html-validate",
        "version": _VERSION,
        "bin": {"html-validate": "bin/html-validate.mjs"},
        "exports": {".": {"node": {"import": "./dist/esm/index.js"}}},
    }
    fields.update(manifest)
    (package / "package.json").write_text(json.dumps(fields))
    link = root / "node_modules" / ".bin" / "html-validate"
    link.parent.mkdir()
    link.symlink_to("../html-validate/bin/html-validate.mjs")
    return link


def _fake_node(tmp_path: Path, *, version: str = _VERSION) -> Path:
    """Write the executable stand-in for ``node``.

    Args:
        tmp_path: Where to put it.
        version: The html-validate version it reports loading.

    Returns:
        Its path.
    """
    node = tmp_path / "fake-node"
    node.write_text(
        f"#!{sys.executable}\nVERSION = {version!r}\n{_FAKE_NODE}",
        encoding="utf-8",
    )
    node.chmod(0o755)
    return node


def test_a_linked_package_is_located_with_its_own_version(tmp_path: Path) -> None:
    """A ``node_modules/.bin`` link leads to the package and its entry.

    Args:
        tmp_path: Project directory.
    """
    link = _package(tmp_path)

    located = locate_package([str(link)])

    package = Path(os.path.realpath(tmp_path)) / "node_modules" / "html-validate"
    assert_that(located).is_equal_to(
        HtmlValidatePackage(
            directory=package,
            version=_VERSION,
            entry=package / "dist" / "esm" / "index.js",
        ),
    )


@pytest.mark.parametrize(
    ("bin_text", "manifest"),
    [
        pytest.param(
            _BIN + 'import "./patched.js";\n',
            {},
            id="bin-runs-more-than-the-cli",
        ),
        pytest.param(
            _BIN.replace("env node", "env bun"),
            {},
            id="bin-runs-another-runtime",
        ),
        pytest.param(_BIN, {"name": "html-validate-fork"}, id="another-package"),
        pytest.param(
            _BIN,
            {"bin": {"html-validate": "bin/other.mjs"}},
            id="bin-entry-elsewhere",
        ),
        pytest.param(
            _BIN,
            {"exports": {".": {"node": {"import": "./dist/esm/other.js"}}}},
            id="another-api-entry",
        ),
        pytest.param(_BIN, {"version": 11}, id="version-not-a-string"),
    ],
)
def test_a_package_that_may_differ_from_the_binary_is_not_used(
    tmp_path: Path,
    bin_text: str,
    manifest: dict[str, object],
) -> None:
    """Anything that could make the API differ from the binary keeps the pool.

    Args:
        tmp_path: Project directory.
        bin_text: The package's bin entry.
        manifest: ``package.json`` overrides.
    """
    link = _package(tmp_path, bin_text=bin_text, **manifest)

    assert_that(locate_package([str(link)])).is_none()


def test_a_wrapper_script_is_not_followed_to_a_package(tmp_path: Path) -> None:
    """A shell wrapper is not the package's bin, so the pool runs it.

    Args:
        tmp_path: Project directory.
    """
    _package(tmp_path / "real")
    wrapper = tmp_path / "html-validate"
    wrapper.write_text(f'#!/bin/sh\nexec "{tmp_path}/real/node_modules/.bin/x" "$@"\n')

    assert_that(locate_package([str(wrapper)])).is_none()


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["bunx", f"html-validate@{_VERSION}"], id="bunx"),
        pytest.param(
            ["npx", "--yes", "--package", f"html-validate@{_VERSION}", "html-validate"],
            id="npx",
        ),
        pytest.param(["node_modules/.bin/html-validate"], id="relative"),
        pytest.param([], id="empty"),
    ],
)
def test_runner_and_relative_commands_keep_the_pool(
    tmp_path: Path,
    command: list[str],
) -> None:
    """Only a single absolute binary path can name the package it runs.

    Args:
        tmp_path: Project directory, holding a valid package.
        command: A resolved command that is not one absolute path.
    """
    _package(tmp_path)

    assert_that(one_process_runtime(command)).is_none()


def _run(
    tmp_path: Path,
    files: dict[str, str],
    *,
    timeout: float = 10.0,
) -> OneProcessRun:
    """Write files and run the stand-in script over them.

    Args:
        tmp_path: Where to write.
        files: Name to content, in order.
        timeout: The per-file deadline.

    Returns:
        The run.
    """
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return run_one_process(
        [str(_fake_node(tmp_path))],
        entry=tmp_path / "index.js",
        expected_version=_VERSION,
        files=[str(tmp_path / name) for name in files],
        cwd=str(tmp_path),
        timeout=timeout,
    )


def test_a_run_records_verdicts_and_errors_in_order(tmp_path: Path) -> None:
    """Each file gets its verdict; one without a verdict keeps its error.

    Args:
        tmp_path: Working directory.
    """
    run = _run(
        tmp_path,
        {"[a].html": _DIRTY, "[b].html": _BOOM, "[c].html": _IGNORED},
    )

    assert_that(run.version).is_equal_to(_VERSION)
    assert_that(run.reached).is_equal_to(3)
    assert_that(run.errors).is_equal_to({1: "Error: boom"})
    assert_that(sorted(run.verdicts)).is_equal_to([0, 2])
    assert_that(run.verdicts[0].valid).is_false()
    assert_that(json.loads(run.verdicts[0].report)[0]["filePath"]).is_equal_to(
        str(tmp_path / "[a].html"),
    )
    assert_that(run.verdicts[2]).is_equal_to(FileVerdict(ignored=True))
    assert_that(run.timed_out_at).is_none()


def test_each_file_has_its_own_deadline(tmp_path: Path) -> None:
    """A hung file stops the run; files before it keep their verdicts.

    Args:
        tmp_path: Working directory.
    """
    run = _run(
        tmp_path,
        {"[a].html": "<p>a</p>", "[b].html": _SLOW, "[c].html": "<p>c</p>"},
        timeout=2.0,
    )

    assert_that(sorted(run.verdicts)).is_equal_to([0])
    assert_that(run.timed_out_at).is_equal_to(1)


def test_a_crash_ends_the_run_where_it_stopped(tmp_path: Path) -> None:
    """A script that exits early settles only the files it reported.

    Args:
        tmp_path: Working directory.
    """
    run = _run(tmp_path, {"[a].html": "<p>a</p>", "[b].html": _CRASH})

    assert_that(sorted(run.verdicts)).is_equal_to([0])
    assert_that(run.reached).is_equal_to(1)
    assert_that(run.failure).is_equal_to("html-validate exited early")
    assert_that(run.timed_out_at).is_none()


def test_a_version_other_than_the_packages_settles_nothing(tmp_path: Path) -> None:
    """The run is refused when the loaded API is not the package's version.

    Args:
        tmp_path: Working directory.
    """
    (tmp_path / "[a].html").write_text("<p>a</p>", encoding="utf-8")

    run = run_one_process(
        [str(_fake_node(tmp_path, version="10.0.0"))],
        entry=tmp_path / "index.js",
        expected_version=_VERSION,
        files=[str(tmp_path / "[a].html")],
        cwd=str(tmp_path),
        timeout=10.0,
    )

    assert_that(run.version).is_none()
    assert_that(run.verdicts).is_empty()
    assert_that(run.failure).contains("10.0.0", _VERSION)


def _stdin_answer(source: str) -> SubprocessResult:
    """Answer one stdin call as html-validate 11.21 would.

    Args:
        source: What the call read on stdin.

    Returns:
        The report for ``/dev/stdin``.
    """
    dirty = "<div>" in source
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
        if dirty
        else []
    )
    stdout = json.dumps([{"filePath": "/dev/stdin", "messages": messages}])
    return SubprocessResult(
        returncode=1 if dirty else 0,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


def _check(
    tmp_path: Path,
    files: dict[str, str],
    *,
    runtime: OneProcessRuntime | None,
    stdin_sources: list[str],
    timeout: float = 10.0,
) -> ToolResult:
    """Run ``check`` with a given one-process runtime and a fake stdin route.

    Args:
        tmp_path: Working directory.
        files: Name to content, in order.
        runtime: The one-process runtime, or None for none.
        stdin_sources: Receives the content of every stdin call.
        timeout: The per-call deadline.

    Returns:
        The check result.
    """
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    ctx = PreparedExecution(
        files=[str(tmp_path / name) for name in files],
        rel_files=list(files),
        cwd=str(tmp_path),
        timeout=timeout,
    )

    def stdin_call(**kwargs: object) -> SubprocessResult:
        """Record and answer one stdin call.

        Args:
            **kwargs: The plugin's subprocess arguments.

        Returns:
            The answer.
        """
        cmd, descriptor = kwargs["cmd"], kwargs["stdin"]
        assert_that(cmd).is_instance_of(list)
        assert_that(cmd[-1]).is_equal_to("--stdin")  # type: ignore[index]
        assert_that(descriptor).is_instance_of(int)
        source = os.read(descriptor, 65536).decode()  # type: ignore[arg-type]
        stdin_sources.append(source)
        return _stdin_answer(source)

    plugin = HtmlValidatePlugin()
    with (
        patch.object(plugin, "prepare", return_value=ctx),
        patch.object(
            plugin,
            "_get_executable_command",
            return_value=["/pinned/html-validate"],
        ),
        patch.object(plugin, "_run_subprocess_result", side_effect=stdin_call),
        patch.object(definition, "one_process_runtime", return_value=runtime),
    ):
        return plugin.check([str(tmp_path)], {})


def _runtime(tmp_path: Path, *, node: Path | None = None) -> OneProcessRuntime:
    """Build a runtime that runs the stand-in as ``node``.

    Args:
        tmp_path: Where the stand-in lives.
        node: A ``node`` to use instead of the stand-in.

    Returns:
        The runtime.
    """
    return OneProcessRuntime(
        node=str(node or _fake_node(tmp_path)),
        package=HtmlValidatePackage(
            directory=tmp_path,
            version=_VERSION,
            entry=tmp_path / "index.js",
        ),
    )


_PAGES = {"[a].html": _DIRTY, "[b].html": "<p>b</p>", "[c].html": _DIRTY}


def test_one_process_results_equal_stdin_results(tmp_path: Path) -> None:
    """A one-process run gives the stdin route's result, field for field.

    Args:
        tmp_path: Working directory.
    """
    (tmp_path / "one").mkdir()
    (tmp_path / "pool").mkdir()
    one_sources: list[str] = []
    pool_sources: list[str] = []

    one = _check(
        tmp_path / "one",
        _PAGES,
        runtime=_runtime(tmp_path),
        stdin_sources=one_sources,
    )
    pooled = _check(tmp_path / "pool", _PAGES, runtime=None, stdin_sources=pool_sources)

    assert_that(one_sources).is_empty()
    assert_that(pool_sources).is_length(3)
    assert_that(one.issues_count).is_equal_to(2)
    relocated = [
        (Path(issue.file).relative_to(tmp_path / "one"), issue.line, issue.message)
        for issue in one.issues
    ]
    assert_that(relocated).is_equal_to(
        [
            (Path(issue.file).relative_to(tmp_path / "pool"), issue.line, issue.message)
            for issue in pooled.issues
        ],
    )
    assert_that(
        (one.success, one.execution_failed, one.inspected_files),
    ).is_equal_to((pooled.success, pooled.execution_failed, pooled.inspected_files))
    assert_that(one.output).is_equal_to(
        (pooled.output or "").replace(str(tmp_path / "pool"), str(tmp_path / "one")),
    )


def test_files_without_a_verdict_go_to_the_stdin_pool(tmp_path: Path) -> None:
    """An error or a crash sends only the unsettled files to stdin.

    Args:
        tmp_path: Working directory.
    """
    sources: list[str] = []
    result = _check(
        tmp_path,
        {
            "[a].html": _DIRTY,
            "[b].html": f"<p><div></p>{_BOOM}",
            "[c].html": "<p>c</p>",
            "[d].html": f"<p><div></p>{_CRASH}",
            "[e].html": _DIRTY,
        },
        runtime=_runtime(tmp_path),
        stdin_sources=sources,
    )

    assert_that(sorted(sources)).is_equal_to(
        sorted([f"<p><div></p>{_BOOM}", f"<p><div></p>{_CRASH}", _DIRTY]),
    )
    assert_that(result.execution_failed).is_false()
    assert_that([Path(issue.file).name for issue in result.issues]).is_equal_to(
        ["[a].html", "[b].html", "[d].html", "[e].html"],
    )


def test_a_runtime_that_cannot_start_falls_back_to_the_pool(tmp_path: Path) -> None:
    """A ``node`` that cannot be launched leaves every file to stdin.

    Args:
        tmp_path: Working directory.
    """
    sources: list[str] = []
    result = _check(
        tmp_path,
        _PAGES,
        runtime=_runtime(tmp_path, node=tmp_path / "missing-node"),
        stdin_sources=sources,
    )

    assert_that(sorted(sources)).is_equal_to(sorted(_PAGES.values()))
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.execution_failed).is_false()


def test_a_one_process_timeout_names_the_file_and_starts_nothing_else(
    tmp_path: Path,
) -> None:
    """After the deadline no stdin call starts; earlier findings stay.

    Args:
        tmp_path: Working directory.
    """
    sources: list[str] = []
    result = _check(
        tmp_path,
        {
            "[a].html": _DIRTY,
            "[b].html": _BOOM,
            "[c].html": _SLOW,
            "[d].html": _DIRTY,
            "[e].html": _DIRTY,
        },
        runtime=_runtime(tmp_path),
        stdin_sources=sources,
        timeout=2.0,
    )

    assert_that(sources).is_empty()
    assert_that(result.execution_failed).is_true()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(len(result.issues)).is_equal_to(1)
    assert_that(result.output).contains(
        f"{tmp_path / '[b].html'}: html-validate gave no verdict",
        "Error: boom",
        f"{tmp_path / '[c].html'}: ",
        "timed out",
        "not run on 2 file(s) because an earlier html-validate call timed out: "
        "'[d].html', '[e].html'.",
    )


def test_a_file_html_validate_ignores_adds_nothing(tmp_path: Path) -> None:
    """A file the API reports ignored is dropped from the merged result.

    Args:
        tmp_path: Working directory.
    """
    sources: list[str] = []
    result = _check(
        tmp_path,
        {"[a].html": _DIRTY, "[b].html": f"<p><div></p>{_IGNORED}"},
        runtime=_runtime(tmp_path),
        stdin_sources=sources,
    )

    assert_that(sources).is_empty()
    assert_that(result.execution_failed).is_false()
    assert_that([Path(issue.file).name for issue in result.issues]).is_equal_to(
        ["[a].html"],
    )
