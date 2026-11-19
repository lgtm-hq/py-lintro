"""OSV-Scanner runs without a complete scan report (#3352).

Each process below is what osv-scanner 2.4.0 or 2.7.1 prints for the case at
``--verbosity error``, where a scan that went well prints nothing.
"""

from __future__ import annotations

import json
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

_NO_SOURCES_STDERR = "No package sources found, --help for usage information.\n"

_LOCKFILE: dict[str, str] = {"requirements.txt": "flask==3.0.0\n"}

#: What osv-scanner 2.7.1 logs when its vulnerability queries fail.
_QUERY_FAILED_STDERR = (
    "Error during extraction: (extracting as vulnmatch/osvdev) max retries "
    "exceeded: attempt 4: request failed: Post "
    '"https://api.osv.dev/v1/querybatch": proxyconnect tcp: dial tcp '
    "127.0.0.1:9: connect: connection refused\n"
)

#: What osv-scanner 2.4.0 logs for the same failure.
_QUERY_FAILED_STDERR_2_4 = (
    "error when retrieving vulns: max retries exceeded: attempt 4: "
    "request failed: connection refused\n"
)

_BAD_CONFIG_STDERR = (
    "Failed to read config file: toml: line 2: expected end of table array "
    "name delimiter ']', but got '\\n' instead\n"
    "toml: line 2: expected end of table array name delimiter ']', but got "
    "'\\n' instead\n"
)


def _run(
    plugin: OsvScannerPlugin,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    files: dict[str, str] | None = None,
) -> ToolResult:
    """Run the plugin's check against one mocked osv-scanner process.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory scanned.
        returncode: Exit status of osv-scanner.
        stdout: Its standard output.
        stderr: Its standard error.
        files: Files to create under ``tmp_path``; a requirements.txt when
            omitted.

    Returns:
        The plugin's result.
    """
    for name, content in (files if files is not None else _LOCKFILE).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(tmp_path)], {})


def _extraction_error(tmp_path: Path, name: str, message: str) -> str:
    """Return the line osv-scanner prints for a source it could not extract.

    Args:
        tmp_path: Directory scanned.
        name: The source's name under ``tmp_path``.
        message: The extractor's error.

    Returns:
        The line, with the path relative to ``/`` as osv-scanner prints it.
    """
    source = str(tmp_path / name).lstrip("/")
    return f"Error during extraction: (extracting as {name}) {source}: {message}\n"


def _vulnerable_report(source: Path) -> str:
    """Return an osv-scanner report naming one vulnerable package.

    Args:
        source: The lockfile the finding is reported for.

    Returns:
        The JSON report.
    """
    return json.dumps(
        {
            "results": [
                {
                    "source": {"path": str(source), "type": "lockfile"},
                    "packages": [
                        {
                            "package": {
                                "name": "flask",
                                "version": "3.0.0",
                                "ecosystem": "PyPI",
                            },
                            "vulnerabilities": [{"id": "GHSA-68rp-wp8r-4726"}],
                            "groups": [{"ids": ["GHSA-68rp-wp8r-4726"]}],
                        },
                    ],
                },
            ],
        },
    )


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
        files={},
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
    assert_that(result.output).is_equal_to(
        "OSV-Scanner printed no report (exit code 127).\n" + _BAD_CONFIG_STDERR.strip(),
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "reason", "parse_failures"),
    [
        (0, "", "OSV-Scanner printed no report (exit code 0).", None),
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
            "OSV-Scanner reported an error (exit code 1), so the scan is "
            "incomplete. Its standard error:\nnetwork unreachable",
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


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        (
            "requirements.txt",
            "flask==3.0.0\n",
            "Open(requirements.txt): open requirements.txt: permission denied",
        ),
        (
            "package-lock.json",
            "not json{\n",
            "could not extract: invalid character 'o' in literal null "
            "(expecting 'u')",
        ),
    ],
    ids=["unreadable", "malformed"],
)
def test_source_that_cannot_be_extracted_is_not_a_no_files_run(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    name: str,
    content: str,
    message: str,
) -> None:
    """A lockfile osv-scanner could not read is an incomplete run (#3352).

    osv-scanner prints "No package sources found" here too, exactly as for an
    empty directory; only the extraction error tells the cases apart.
    """
    stderr = (
        _extraction_error(tmp_path, name, message)
        + "No package sources found, --help for usage information.\n"
    )

    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=128,
        stderr=stderr,
        files={name: content},
    )

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_equal_to(
        f"OSV-Scanner printed no report (exit code 128).\n{stderr.strip()}",
    )


def test_readable_lockfile_beside_a_broken_one_keeps_its_findings(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Findings from the lockfile read survive; the run is still incomplete."""
    stderr = _extraction_error(
        tmp_path,
        "package-lock.json",
        "could not extract: invalid character 'g' looking for beginning of value",
    )

    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=1,
        stdout=_vulnerable_report(tmp_path / "requirements.txt"),
        stderr=stderr,
        files={**_LOCKFILE, "package-lock.json": "garbage{\n"},
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(0)
    assert_that(result.issues).extracting("vuln_id").is_equal_to(
        ["GHSA-68rp-wp8r-4726"],
    )
    assert_that(result.output).ends_with(
        "package-lock.json: could not extract: invalid character 'g' looking "
        "for beginning of value",
    )


def test_extraction_error_under_an_excluded_path_keeps_the_verdict(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """A broken source lintro excludes does not make the scan incomplete."""
    osv_scanner_plugin.set_options(exclude_patterns=["vendor"])
    stderr = _extraction_error(
        tmp_path,
        "vendor/package-lock.json",
        "could not extract: invalid character 'g' looking for beginning of value",
    )

    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=1,
        stdout=_vulnerable_report(tmp_path / "requirements.txt"),
        stderr=stderr,
        files={**_LOCKFILE, "vendor/package-lock.json": "garbage{\n"},
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


@pytest.mark.parametrize(
    ("returncode", "stderr", "quoted"),
    [
        (127, _QUERY_FAILED_STDERR, "max retries exceeded"),
        (1, _QUERY_FAILED_STDERR, "max retries exceeded"),
        (127, _QUERY_FAILED_STDERR_2_4, "error when retrieving vulns"),
    ],
    ids=["exit-127", "exit-1", "exit-127-2.4-wording"],
)
def test_failed_exit_with_an_empty_report_is_not_a_clean_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    returncode: int,
    stderr: str,
    quoted: str,
) -> None:
    """An empty ``results`` list beside a failed exit proves no scan (#3352)."""
    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=returncode,
        stdout='{"results": [], "experimental_config": {}}',
        stderr=stderr,
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).starts_with(
        f"OSV-Scanner reported an error (exit code {returncode})",
    ).contains(quoted)


def test_failed_exit_beside_findings_keeps_them_and_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Only exit 1 is a "vulnerabilities found" verdict."""
    result = _run(
        osv_scanner_plugin,
        tmp_path,
        returncode=127,
        stdout=_vulnerable_report(tmp_path / "requirements.txt"),
        stderr=_QUERY_FAILED_STDERR_2_4,
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).starts_with(
        "OSV-Scanner reported an error (exit code 127)",
    ).contains("error when retrieving vulns")
