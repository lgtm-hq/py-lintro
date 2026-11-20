"""A security scanner whose version cannot be read fails the run (#3568).

The version gate could not apply the minimum or the known-bad list to a
version it could not read, yet treated the tool as passing. A security scanner
in that state is now refused through the same path as a known-bad release:
the run fails, every report says the scanner did not run, and the message
quotes what the tool printed. ``LINTRO_ALLOW_VERSION_LAG`` does not apply: an
unknown version cannot be shown to be merely lagging. Any other tool runs as
before.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_recommended_version
from lintro.enums.action import Action
from lintro.models.core.tool_outcome import Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core import snapshots
from lintro.tools.core.version_parsing import printed_output_excerpt
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.tools.shellcheck.definition import ShellcheckPlugin
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import render_csv_report, render_markdown_report
from lintro.utils.output.sarif import execution_notifications

#: What a source build of a Go scanner prints when no release was stamped in.
_DEV_OUTPUT = "osv-scanner version: dev\ncommit: n/a"

#: The same output as the message quotes it: one line, escaped.
_QUOTED = '"osv-scanner version: dev commit: n/a"'


def _fake_tool(directory: Path, name: str, output: str) -> Path:
    """Write an executable that prints ``output`` and counts its runs.

    Args:
        directory: Directory to create the executable in.
        name: Executable name.
        output: What the executable prints, for any arguments.

    Returns:
        Path to the executable.
    """
    binary = directory / name
    printed = output.replace("'", "'\\''")
    binary.write_text(
        f"#!/bin/sh\necho call >> \"$0.calls\"\nprintf '%s\\n' '{printed}'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _runs(binary: Path) -> int:
    """Count how often a fake tool ran.

    Args:
        binary: The fake executable.

    Returns:
        The number of runs recorded next to it.
    """
    calls = binary.parent / f"{binary.name}.calls"
    return len(calls.read_text().splitlines()) if calls.exists() else 0


@pytest.fixture
def on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str, str], Path]:
    """Put fake tools first on a PATH that holds no other wrapped tool.

    Args:
        tmp_path: Directory for the fake binaries.
        monkeypatch: Sets PATH and clears the version-lag override.

    Returns:
        A function that writes a fake binary with a given output.
    """
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    return lambda name, output: _fake_tool(tmp_path, name, output)


def _refusal(tmp_path: Path) -> ToolResult:
    """Run osv-scanner's version gate in ``tmp_path``.

    Args:
        tmp_path: Execution and cache directory.

    Returns:
        The gate's result; the gate refused the scanner.
    """
    result = verify_tool_version(OsvScannerPlugin().definition, cwd=tmp_path)
    assert result is not None  # narrow: a refused version returns a result
    return result


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("tool dev\n\tbuilt\r\n  today\n", '"tool dev built today"'),
        ('say "hi" \\ \x1b[31mred', '"say \\"hi\\" \\\\ \\u001b[31mred"'),
        ("  \n\t ", "nothing"),
        ("x" * 205, f'"{"x" * 200}..."'),
    ],
    ids=["one-line", "escaped", "empty", "trimmed"],
)
def test_printed_output_is_quoted_on_one_escaped_line(
    output: str,
    expected: str,
) -> None:
    """What the tool printed is shown on one line, trimmed and escaped.

    Args:
        output: What the tool printed.
        expected: How the message quotes it.
    """
    assert_that(printed_output_excerpt(output)).is_equal_to(expected)


@pytest.mark.parametrize(
    "allow_lag",
    [None, "*", "osv_scanner"],
    ids=["no-override", "override-all", "override-this-tool"],
)
def test_scanner_with_unreadable_version_fails_the_run(
    tmp_path: Path,
    on_path: Callable[[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    allow_lag: str | None,
) -> None:
    """The scanner is refused, the message quotes its output, no override helps.

    Args:
        tmp_path: Execution and cache directory.
        on_path: Writes fake binaries first on PATH.
        monkeypatch: Sets the version-lag override.
        allow_lag: Value of ``LINTRO_ALLOW_VERSION_LAG``, or None for unset.
    """
    on_path("osv-scanner", _DEV_OUTPUT)
    if allow_lag is not None:
        monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", allow_lag)
    pinned = get_recommended_version("osv_scanner")

    result = _refusal(tmp_path)

    execution = result.outcome.execution
    assert isinstance(execution, Unavailable)  # narrow for the attribute reads
    assert_that(execution.strict).is_true()
    assert_that(result.success).is_false()
    assert_that(execution.reason).starts_with(
        "security scanner did not run: Could not read a version from the "
        "output of `osv-scanner --version`, which printed "
        f"{_QUOTED}.",
    )
    assert_that(result.output).contains(
        "security scanner did not run, so nothing was scanned",
        _QUOTED,
        "LINTRO_ALLOW_VERSION_LAG does not apply",
        f"Install {pinned}, the version lintro tests.",
    )
    assert_that(result.output).does_not_contain("\n")


def test_other_tool_with_unreadable_version_still_runs(
    tmp_path: Path,
    on_path: Callable[[str, str], Path],
) -> None:
    """A tool that is not a security scanner keeps today's behaviour: it runs.

    Args:
        tmp_path: Execution and cache directory.
        on_path: Writes fake binaries first on PATH.
    """
    binary = on_path("shellcheck", "ShellCheck dev build")

    result = verify_tool_version(ShellcheckPlugin().definition, cwd=tmp_path)

    assert_that(result).is_none()
    assert_that(_runs(binary)).is_equal_to(1)


def test_missing_scanner_keeps_the_missing_tool_result(
    tmp_path: Path,
    on_path: Callable[[str, str], Path],
) -> None:
    """A scanner that is not installed is reported missing, not version-unknown.

    Args:
        tmp_path: Execution and cache directory.
        on_path: Restricts PATH to a directory without the scanner.
    """
    result = _refusal(tmp_path)

    assert_that(result.unavailable_reason).contains("not found")
    assert_that(result.unavailable_reason).does_not_contain(
        "security scanner did not run",
        "Could not read a version",
    )


def test_every_report_says_the_scanner_did_not_run(
    tmp_path: Path,
    on_path: Callable[[str, str], Path],
) -> None:
    """Markdown, CSV, JSON and SARIF state the refusal; none reads as a pass.

    Args:
        tmp_path: Execution and cache directory.
        on_path: Writes fake binaries first on PATH.
    """
    on_path("osv-scanner", _DEV_OUTPUT)
    result = _refusal(tmp_path)
    statement = "osv_scanner did not run, so nothing was checked."

    markdown = render_markdown_report([result], Action.CHECK)
    rows = list(csv.DictReader(io.StringIO(render_csv_report([result]))))
    data = serialize_tool_result(result, action=Action.CHECK)
    notes = [n["message"]["text"] for n in execution_notifications([result])]

    assert_that(markdown).contains("| osv_scanner | did not run |", _QUOTED)
    assert_that(markdown).does_not_contain("No issues found")
    assert_that(rows[0]["notes"]).starts_with(statement).contains(_QUOTED)
    assert_that(data).contains_entry({"status": "unavailable"}, {"success": False})
    assert_that(data["unavailable_reason"]).contains(_QUOTED)
    assert_that(notes).is_length(1)
    assert_that(notes[0]).starts_with(statement).contains(_QUOTED)


@pytest.mark.parametrize("old_format", [False, True], ids=["current", "old-format"])
def test_cached_unreadable_version_is_refused_again(
    tmp_path: Path,
    on_path: Callable[[str, str], Path],
    old_format: bool,
) -> None:
    """A cached entry never turns an unreadable scanner version into a pass.

    A current entry records ``version_unreadable`` and is refused without a
    probe; one written before the field existed is probed again.

    Args:
        tmp_path: Execution and cache directory.
        on_path: Writes fake binaries first on PATH.
        old_format: Whether the cached entry lacks ``version_unreadable``.
    """
    binary = on_path("osv-scanner", _DEV_OUTPUT)
    _refusal(tmp_path)
    if old_format:
        cache_file = snapshots._cache_file_path(cache_root=tmp_path)
        data = json.loads(cache_file.read_text())
        del data["snapshots"]["osv_scanner"]["version_unreadable"]
        cache_file.write_text(json.dumps(data))
    probes = _runs(binary)
    with snapshots._cache_lock:
        snapshots._memory_caches.clear()

    result = _refusal(tmp_path)

    assert_that(_runs(binary)).is_equal_to(probes + 1 if old_format else probes)
    assert_that(result.success).is_false()
    assert_that(result.unavailable_reason).contains(_QUOTED)
