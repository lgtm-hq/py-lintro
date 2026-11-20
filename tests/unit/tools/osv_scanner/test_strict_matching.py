"""Each osv-scanner rule matches exactly and completely (#3352).

Reproductions of runs that used to read as complete, and standard error
captured from osv-scanner 2.4.0 and 2.7.1  # meta-guard: allow
for several unsatisfiable or
private manifests, written with ``{root}`` for the scanned directory
(relative to ``/``, as osv-scanner prints it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import run_subprocess
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.utils.meaningful_run import baseline_is_eligible
from lintro.utils.tool_metadata import tool_notes

_EMPTY_REPORT = '{"results": [], "experimental_config": {}}'

_HEADER = "Error during extraction: (extracting as transitivedependency/requirements) "


def _impossible(name: str, package: str = "flask") -> str:
    """Return one unsatisfiable manifest's part, as the resolver prints it.

    Args:
        name: The manifest's directory under the scanned one.
        package: The package no version of satisfies the pin.

    Returns:
        The part, from ``failed resolution for`` to the blank line after it.
    """
    return (
        f"failed resolution for {{root}}/{name}/requirements.txt: resolution "
        f'impossible:\nno candidates at all for: {package} "==99999.0.0"\n\n'
    )


_PRIVATE = (
    "failed resolution for {root}/p/requirements.txt: package "
    "System(7):zz-private-pkg-3352: not found\n"
)

#: Captured standard error, per case and version. Each manifest pins a
#: version that does not exist (``flask==99999.0.0``); ``p`` also lists a
#: package public PyPI does not have.
_CAPTURES: dict[str, str] = {
    "two-2.4.0": _HEADER + _impossible("a") + _impossible("b"),
    "two-2.7.1": _HEADER + _impossible("a") + _impossible("b"),  # meta-guard: allow
    "three-2.4.0": (
        _HEADER + _impossible("a") + _impossible("c") + _impossible("b", "requests")
    ),
    "three-2.7.1": (  # meta-guard: allow
        _HEADER + _impossible("a") + _impossible("b", "requests") + _impossible("c")
    ),
    "mix-2.4.0": _HEADER + _impossible("a") + _impossible("c", "django") + _PRIVATE,
    "mix-2.7.1": (  # meta-guard: allow
        _HEADER + _impossible("a") + _impossible("c", "django") + _PRIVATE
    ),
}

#: The manifests each case names.
_CAPTURE_FILES: dict[str, tuple[str, ...]] = {
    "two": ("a", "b"),
    "three": ("a", "b", "c"),
    "mix": ("a", "c", "p"),
}

#: Two pins no version satisfies together (``flask==2.0.0`` and
#: ``werkzeug==1.0.0``), 2.4.0 and 2.7.1 alike,  # meta-guard: allow
#: at exit 1 with findings.
_CONFLICT_STDERR = (
    f"{_HEADER}failed resolution for {{root}}/requirements.txt: resolution "
    'impossible:\nrequirements conflict: werkzeug: "==1.0.0,>=2.0"\n\n'
)


def _check(
    plugin: OsvScannerPlugin,
    root: Path,
    *,
    returncode: int,
    stderr: str = "",
    stdout: str = _EMPTY_REPORT,
) -> ToolResult:
    """Run the plugin's check against one recorded osv-scanner process.

    Args:
        plugin: Plugin under test.
        root: The scanned directory.
        returncode: Exit status of osv-scanner.
        stderr: Its standard error, with ``{root}`` for the scanned directory.
        stdout: Its standard output.

    Returns:
        The plugin's result.
    """
    text = stderr.replace("{root}", str(root).lstrip("/"))
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=text,
        output=stdout + text,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(root)], {"check_suppressions": False})


def _assert_incomplete(result: ToolResult) -> None:
    """Assert a run is incomplete and cannot update a baseline.

    Args:
        result: The plugin's result.
    """
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.no_files).is_false()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()


def test_excluded_file_error_naming_an_included_file_is_kept(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """An excluded file's error is not dropped when it names another file."""
    for name in ("vendor/package-lock.json", "app/package-lock.json"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}\n")
    osv_scanner_plugin.set_options(exclude_patterns=["vendor"])
    stderr = (
        "Error during extraction: (extracting as javascript/packagelockjson) "
        "{root}/vendor/package-lock.json: failed reading "
        "{root}/app/package-lock.json: rpc error: unavailable\n"
    )

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        returncode=127,
        stderr=stderr,
        stdout='{"results":[]}',
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(
        f"{str(tmp_path).lstrip('/')}/app/package-lock.json: rpc error: unavailable",
    )


@pytest.mark.parametrize(
    "stdout",
    [
        'garbage {"results": []} trailing rpc error',
        '{"results": []} {"error":"failed"}',
        '{"error": "failed", "results": []}',
    ],
)
def test_stdout_that_is_more_than_one_report_at_exit_0_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    stdout: str,
) -> None:
    """Exit 0 with nothing on standard error still needs a whole report."""
    result = _check(osv_scanner_plugin, tmp_path, returncode=0, stdout=stdout)

    _assert_incomplete(result)
    assert_that(result.output).starts_with("OSV-Scanner's report could not be read")


def test_no_sources_line_with_more_text_is_not_no_files(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Only the exact whole line means nothing was there to scan."""
    line = (
        "No package sources found, --help for usage information. "
        "rpc error: unavailable"
    )

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        returncode=128,
        stderr=f"{line}\n",
        stdout="",
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(line)


@pytest.mark.parametrize("case", sorted(_CAPTURES))
def test_several_unsatisfiable_manifests_are_a_complete_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    case: str,
) -> None:
    """Real captures: blank lines between parts keep the message together."""
    names = _CAPTURE_FILES[case.split("-", 1)[0]]
    for name in names:
        (tmp_path / name).mkdir()
        (tmp_path / name / "requirements.txt").write_text("flask==99999.0.0\n")

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        returncode=127,
        stderr=_CAPTURES[case],
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_true()
    notes = tool_notes(result.metadata)
    assert_that(notes).is_length(1)
    # The note is an excerpt; it always starts with the first manifest.
    assert_that(notes[0]["message"]).contains(f"{names[0]}/requirements.txt")


def test_requirements_conflict_beside_a_finding_is_a_complete_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """The real conflict message carries a requirement specifier."""
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("flask==2.0.0\nwerkzeug==1.0.0\n")
    package = {"name": "werkzeug", "version": "1.0.0", "ecosystem": "PyPI"}
    vulnerable = {
        "package": package,
        "vulnerabilities": [{"id": "GHSA-2g68-c3qc-8985"}],
        "groups": [{"ids": ["GHSA-2g68-c3qc-8985"]}],
    }
    source = {"path": str(manifest), "type": "lockfile"}
    report = {"results": [{"source": source, "packages": [vulnerable]}]}

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        returncode=1,
        stderr=_CONFLICT_STDERR,
        stdout=json.dumps(report),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(1)


_UNDECODABLE_SCRIPT = """\
import sys
sys.stdout.write('{"results": []}')
sys.stderr.buffer.write(b"failed reading calf\\xe9.lock: rpc error\\n")
sys.exit(127)
"""


def test_undecodable_stderr_is_quoted_with_replacement(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Bytes that are not UTF-8 still leave the error readable and quoted."""
    script = tmp_path / "fake_osv_scanner.py"
    script.write_text(_UNDECODABLE_SCRIPT)

    with patch.object(
        osv_scanner_plugin,
        "_get_executable_command",
        return_value=[sys.executable, str(script)],
    ):
        result = osv_scanner_plugin.check(
            [str(tmp_path)],
            {"check_suppressions": False},
        )

    _assert_incomplete(result)
    assert_that(result.output).contains("failed reading calf�.lock: rpc error")


def test_other_tools_keep_strict_decoding(tmp_path: Path) -> None:
    """Only a caller that asks for replacement gets it."""
    script = tmp_path / "undecodable.py"
    script.write_text(_UNDECODABLE_SCRIPT)
    cmd = [sys.executable, str(script)]

    with pytest.raises(UnicodeDecodeError):
        run_subprocess(cmd, 60)
    replaced = run_subprocess(cmd, 60, decode_errors="replace")

    assert_that(replaced.stderr).contains("calf�.lock")
    assert_that(replaced.returncode).is_equal_to(127)
