"""OSV-Scanner runs without a complete scan report (#3352).

Each process below is what a current osv-scanner v2 really prints for the case.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.tools.osv_scanner.definition import (
    OSV_NO_SOURCES_MESSAGE,
    OsvScannerPlugin,
)

_NO_SOURCES_STDERR = (
    "Scanning dir /repo\n"
    "Starting filesystem walk for root: /\n"
    "No package sources found, --help for usage information.\n"
)

_BAD_CONFIG_STDERR = (
    "Failed to read config file: toml: line 2: expected end of table array "
    "name delimiter ']', but got '\\n' instead\n"
)


def _run(
    plugin: OsvScannerPlugin,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> ToolResult:
    """Run the plugin's check against one mocked osv-scanner process.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory holding the scanned lockfile.
        returncode: Exit status of osv-scanner.
        stdout: Its standard output.
        stderr: Its standard error.

    Returns:
        The plugin's result.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("six==1.17.0\n")
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(lockfile)], {})


def test_no_package_sources_is_a_no_files_run(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Nothing to scan is no work, told apart from a scan in the summary."""
    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=128,
        stderr=_NO_SOURCES_STDERR,
    )

    assert_that(result.no_files).is_true()
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.output).is_equal_to(OSV_NO_SOURCES_MESSAGE)


def test_config_error_names_the_error_and_claims_no_inspection(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """A run that could not read its config is incomplete, not a scan."""
    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=127,
        stderr=_BAD_CONFIG_STDERR,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).starts_with(
        "OSV-Scanner exited with an error and printed no report: "
        "Failed to read config file",
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "reason", "parse_failures"),
    [
        (0, "", "OSV-Scanner printed no report.", None),
        (
            0,
            '{"error": "failed to load config"}',
            "OSV-Scanner reported an error instead of a scan report: "
            "failed to load config",
            1,
        ),
        (
            0,
            '{"results": "not-a-list"}',
            "OSV-Scanner printed a report that could not be fully parsed",
            1,
        ),
        (
            1,
            '{"results": [{"source": {"path": "requirements.txt"}}]}',
            "OSV-Scanner exited with an error and reported no vulnerabilities: "
            "network unreachable",
            0,
        ),
    ],
    ids=["empty", "error-payload", "bad-results", "failed-exit-no-findings"],
)
def test_incomplete_report_is_an_execution_failure(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    returncode: int,
    stdout: str,
    reason: str,
    parse_failures: int | None,
) -> None:
    """Every incomplete report names its reason and claims no inspection."""
    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=returncode,
        stdout=stdout,
        stderr="network unreachable\n" if returncode else "",
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(parse_failures)
    assert_that(result.output).starts_with(reason)
    assert_that(result.issues).is_empty()
