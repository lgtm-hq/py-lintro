"""Every line osv-scanner prints is recognised whole, at every exit (#3352).

A routine line is matched against the whole line, never by its first words,
and a line that is neither routine nor part of a recognised entry makes the
run incomplete whatever osv-scanner's exit status, with the line quoted and
any real findings kept.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.osv_scanner import OsvScannerIssue
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.tools.osv_scanner.extraction_log import sort_extraction_errors
from lintro.tools.osv_scanner.routine_lines import RoutineLines
from lintro.utils.meaningful_run import baseline_is_eligible
from lintro.utils.tool_metadata import tool_notes
from tests.unit.tools.osv_scanner._captured_stderr import (
    CAPTURES,
    CASE_FILES,
    CONFIG_CAPTURES,
    CONFIG_FILES,
    Capture,
)

_EMPTY_REPORT = '{"results": [], "experimental_config": {}}'

#: Lines that start like a routine line but say something else.
_PREFIX_LOOKALIKES: tuple[str, ...] = (
    "Scanned failed: rpc error",
    "Filtered 0 vulns; error querying: timeout",
    "Warning: enricher transitivedependency/requirements failed: "
    "rpc error: code = Unavailable",
)

#: Failed lookups osv-scanner can print beside a complete-looking report.
_LOOKUP_FAILURES: tuple[str, ...] = (
    "error when retrieving vulns: connection refused",
    "error when retrieving vulns for 3 of 7 packages: context deadline exceeded",
)


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    """Create every manifest and configuration the captured runs scanned.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        The directory standing in for ``{root}``.
    """
    for name, content in {**CASE_FILES, **CONFIG_FILES}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (tmp_path / "empty").mkdir()
    return tmp_path


def _fill(stderr: str, root: Path) -> str:
    """Put the scanned root in place of ``{root}``.

    Args:
        stderr: Captured standard error.
        root: The directory standing in for ``{root}``.

    Returns:
        The standard error text.
    """
    return stderr.replace("{root}", str(root).lstrip("/"))


def _capture(case: str, version: str) -> Capture:
    """Return one captured run.

    Args:
        case: The directory scanned.
        version: The osv-scanner version.

    Returns:
        The capture.
    """
    return next(
        c
        for c in (*CAPTURES, *CONFIG_CAPTURES)
        if c.case == case and c.version == version and c.network == "ok"
    )


def _flask_report(source: Path) -> str:
    """Return an osv-scanner report naming one vulnerable flask pin.

    Args:
        source: The manifest the finding is reported for.

    Returns:
        The JSON report.
    """
    package = {"name": "flask", "version": "3.0.0", "ecosystem": "PyPI"}
    vuln = "PYSEC-2026-2151"
    return json.dumps(
        {
            "results": [
                {
                    "source": {"path": str(source), "type": "lockfile"},
                    "packages": [
                        {
                            "package": package,
                            "vulnerabilities": [{"id": vuln}],
                            "groups": [{"ids": [vuln]}],
                        },
                    ],
                },
            ],
        },
    )


def _check(
    plugin: OsvScannerPlugin,
    root: Path,
    *,
    returncode: int,
    stderr: str,
    stdout: str = _EMPTY_REPORT,
) -> ToolResult:
    """Run the plugin's check against one recorded osv-scanner process.

    Args:
        plugin: Plugin under test.
        root: The scanned directory.
        returncode: Exit status of osv-scanner.
        stderr: Its standard error.
        stdout: Its standard output.

    Returns:
        The plugin's result.
    """
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(root)], {"check_suppressions": False})


def _assert_incomplete_quoting(result: ToolResult, line: str) -> None:
    """Assert a run is incomplete, unusable for a baseline, and quotes a line.

    Args:
        result: The plugin's result.
        line: The line the result must quote.
    """
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).contains(line)
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()


@pytest.mark.parametrize("line", _PREFIX_LOOKALIKES)
@pytest.mark.parametrize("version", ["2.4.0", "2.7.1"])
def test_routine_opening_words_do_not_make_a_line_routine(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    line: str,
    version: str,
) -> None:
    """A private-package run at exit 127 plus a lookalike line is incomplete."""
    private = _capture("private", version)
    stderr = _fill(private.stderr, scan_root) + line + "\n"

    result = _check(
        osv_scanner_plugin,
        scan_root / "private",
        returncode=127,
        stderr=stderr,
    )

    _assert_incomplete_quoting(result, line)


@pytest.mark.parametrize("line", _LOOKUP_FAILURES)
def test_failed_lookup_beside_findings_at_exit_1_keeps_them_and_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    line: str,
) -> None:
    """A real finding does not hide a failed lookup printed beside it."""
    root = scan_root / "privvuln"
    privvuln = _capture("privvuln", "2.7.1")
    stderr = _fill(privvuln.stderr, scan_root) + line + "\n"

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=1,
        stderr=stderr,
        stdout=_flask_report(root / "requirements.txt"),
    )

    _assert_incomplete_quoting(result, line)
    kept = [i.vuln_id for i in result.issues or [] if isinstance(i, OsvScannerIssue)]
    assert_that(kept).is_equal_to(["PYSEC-2026-2151"])


@pytest.mark.parametrize("line", _LOOKUP_FAILURES)
def test_failed_lookup_at_exit_0_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    line: str,
) -> None:
    """A clean exit with an unrecognised line is not a clean scan."""
    clean = _capture("clean", "2.4.0")
    stderr = _fill(clean.stderr, scan_root) + line + "\n"

    result = _check(
        osv_scanner_plugin,
        scan_root / "clean",
        returncode=0,
        stderr=stderr,
    )

    _assert_incomplete_quoting(result, line)
    assert_that(result.issues_count).is_equal_to(0)


@pytest.mark.parametrize("line", _LOOKUP_FAILURES)
def test_failed_lookup_beside_a_harmless_resolution_at_exit_127_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    line: str,
) -> None:
    """Exit 127 with a private package and a failed lookup is incomplete."""
    private = _capture("private", "2.7.1")
    stderr = line + "\n" + _fill(private.stderr, scan_root)

    result = _check(
        osv_scanner_plugin,
        scan_root / "private",
        returncode=127,
        stderr=stderr,
    )

    _assert_incomplete_quoting(result, line)


@pytest.mark.parametrize("version", ["2.4.0", "2.7.1"])
def test_real_run_with_a_configuration_is_a_complete_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    version: str,
) -> None:
    """Suppression, override and unused-entry lines from a real run are routine.

    The configuration filters vulnerabilities (one reason over two lines,
    one with no reason), ignores a package, and holds a duplicate, an
    unused ignore and an unused override.
    """
    root = scan_root / "cfg"
    capture = _capture("cfg", version)

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=capture.returncode,
        stderr=_fill(capture.stderr, scan_root),
        stdout=_flask_report(root / "app" / "requirements.txt"),
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_true()
    notes = tool_notes(result.metadata)
    assert_that([note["summary"] for note in notes]).is_equal_to(
        ["indirect dependencies not resolved"],
    )


@pytest.mark.parametrize(
    ("original", "changed", "quoted"),
    [
        pytest.param(
            "because: flask session issue accepted: not reachable",
            "because: lookup failed: connection refused",
            "lookup failed: connection refused",
            id="reason-not-in-config",
        ),
        pytest.param(
            "PYSEC-2021-66 and 3 aliases",
            "PYSEC-2099-1 and 3 aliases",
            "PYSEC-2099-1 and 3 aliases",
            id="id-not-in-config",
        ),
        pytest.param(
            "second line: still the reason",
            "second line: rpc error",
            "second line: rpc error",
            id="reason-continuation-differs",
        ),
        pytest.param(
            " - GHSA-zzzz-zzzz-zzzz",
            " - rpc error",
            "- rpc error",
            id="unused-entry",
        ),
        pytest.param(
            "/cfg/.osv-scanner.toml has unused ignores:",
            "/other/.osv-scanner.toml has unused ignores:",
            "other/.osv-scanner.toml has unused ignores:",
            id="other-config",
        ),
        pytest.param(
            "found 4 packages",
            "found 4 package",
            "found 4 package",
            id="count-and-noun-disagree",
        ),
        pytest.param(
            "Filtered 6 vulnerabilities from output",
            "Filtered 1 vulnerabilities from output",
            "Filtered 1 vulnerabilities from output",
            id="filtered-count-and-noun-disagree",
        ),
        pytest.param(
            "app/requirements.txt file and found",
            "app/missing.txt file and found",
            "app/missing.txt file and found",
            id="scanned-file-missing",
        ),
        pytest.param(
            "Scanning dir /",
            "Scanning dir /elsewhere/",
            "Scanning dir /elsewhere/",
            id="other-dir",
        ),
    ],
)
def test_config_and_count_lines_must_match_the_run(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    original: str,
    changed: str,
    quoted: str,
) -> None:
    """A routine-looking line whose values the run does not bear out fails."""
    root = scan_root / "cfg"
    capture = _capture("cfg", "2.7.1")
    assert_that(capture.stderr).contains(original)
    stderr = _fill(capture.stderr.replace(original, changed, 1), scan_root)

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=capture.returncode,
        stderr=stderr,
        stdout=_flask_report(root / "app" / "requirements.txt"),
    )

    _assert_incomplete_quoting(result, quoted)
    assert_that(result.issues_count).is_equal_to(1)


@pytest.mark.parametrize("version", ["2.4.0", "2.7.1"])
def test_real_empty_directory_run_is_a_no_files_run(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    version: str,
) -> None:
    """The real no-sources output at exit 128 means there was nothing to scan."""
    capture = _capture("empty", version)
    result = _check(
        osv_scanner_plugin,
        scan_root / "empty",
        returncode=capture.returncode,
        stderr=_fill(capture.stderr, scan_root),
        stdout="",
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_true()
    assert_that(result.output).contains("No package sources found")


def test_empty_directory_run_with_an_unrecognised_line_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
) -> None:
    """Nothing to scan is no verdict when osv-scanner also printed an error."""
    line = _LOOKUP_FAILURES[0]
    capture = _capture("empty", "2.7.1")
    stderr = _fill(capture.stderr, scan_root) + line + "\n"

    result = _check(
        osv_scanner_plugin,
        scan_root / "empty",
        returncode=capture.returncode,
        stderr=stderr,
        stdout="",
    )

    _assert_incomplete_quoting(result, line)


def test_every_line_of_the_real_configuration_runs_is_recognised(
    scan_root: Path,
) -> None:
    """With the run's configuration, no line of either version is unknown."""
    root = scan_root / "cfg"
    routine = RoutineLines.for_run(root, root / ".osv-scanner.toml")
    for version in ("2.4.0", "2.7.1"):
        errors = sort_extraction_errors(
            _fill(_capture("cfg", version).stderr, scan_root),
            root,
            is_excluded=lambda _: False,
            routine=routine,
        )
        assert_that(errors.unrecognised).described_as(version).is_empty()
        assert_that(errors.skipped).described_as(version).is_empty()
        assert_that(errors.notes).described_as(version).is_length(1)


def test_configuration_lines_are_unrecognised_without_the_configuration(
    scan_root: Path,
) -> None:
    """Suppression lines are routine only for the file that was passed."""
    root = scan_root / "cfg"
    errors = sort_extraction_errors(
        _fill(_capture("cfg", "2.7.1").stderr, scan_root),
        root,
        is_excluded=lambda _: False,
        routine=RoutineLines.for_run(root),
    )

    assert_that(errors.unrecognised).contains(
        "PYSEC-2021-66 and 3 aliases have been filtered out because: "
        "(no reason given)",
        " - GHSA-zzzz-zzzz-zzzz",
    )


def test_scanned_line_naming_a_directory_is_routine(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
) -> None:
    """osv-scanner also prints its scanned-file line for a directory.

    Both versions print ``Scanned <root>/lintro/deps/parsers file and found 0
    packages`` for this repository, where ``parsers`` is a directory.
    """
    root = scan_root / "clean"
    (root / "parsers").mkdir()
    clean = _capture("clean", "2.7.1")
    line = f"Scanned {root / 'parsers'} file and found 0 packages\n"

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=0,
        stderr=_fill(clean.stderr, scan_root) + line,
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
