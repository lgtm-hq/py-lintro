"""Unit tests for OSV-Scanner plugin."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.enums.tool_type import ToolType
from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.osv_scanner import (
    OSV_SCANNER_DEFAULT_TIMEOUT,
    OsvScannerPlugin,
)


def _proc(
    *,
    success: bool,
    stdout: str = "",
    stderr: str = "",
    output: str | None = None,
) -> SubprocessResult:
    """Build a SubprocessResult for mocking ``_run_subprocess_result``.

    Args:
        success: Whether the subprocess succeeded (return code 0).
        stdout: Captured standard output (the JSON report stream).
        stderr: Captured standard error (human-readable log lines).
        output: Combined display output; defaults to ``stdout`` when omitted.

    Returns:
        SubprocessResult with the requested streams.
    """
    return SubprocessResult(
        returncode=0 if success else 1,
        stdout=stdout,
        stderr=stderr,
        output=stdout if output is None else output,
    )


# =============================================================================
# Tests for definition
# =============================================================================


def test_definition_name(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Definition has correct name."""
    assert_that(osv_scanner_plugin.definition.name).is_equal_to("osv_scanner")


def test_definition_type(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Definition has correct tool type."""
    assert_that(osv_scanner_plugin.definition.tool_type).is_equal_to(ToolType.SECURITY)


def test_definition_cannot_fix(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Definition reports no fix support."""
    assert_that(osv_scanner_plugin.definition.can_fix).is_false()


def test_default_timeout(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Default timeout has correct value."""
    assert_that(osv_scanner_plugin.options.get("timeout")).is_equal_to(
        OSV_SCANNER_DEFAULT_TIMEOUT,
    )


# =============================================================================
# Tests for set_options validation
# =============================================================================


def test_set_options_validates_timeout_type(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """set_options rejects non-integer timeout."""
    with pytest.raises(ValueError, match="timeout must be an integer"):
        osv_scanner_plugin.set_options(timeout="fast")


def test_set_options_validates_timeout_negative(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """set_options rejects negative timeout."""
    with pytest.raises(ValueError, match="timeout must be positive"):
        osv_scanner_plugin.set_options(timeout=-1)


def test_set_options_validates_timeout_zero(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """set_options rejects zero timeout."""
    with pytest.raises(ValueError, match="timeout must be positive"):
        osv_scanner_plugin.set_options(timeout=0)


def test_set_options_validates_timeout_bool(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """set_options rejects boolean timeout."""
    with pytest.raises(ValueError, match="timeout must be an integer"):
        osv_scanner_plugin.set_options(timeout=True)


# =============================================================================
# Tests for check method
# =============================================================================


def test_check_no_vulnerabilities(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no vulnerabilities found.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_clean_scan_with_log_prefix_and_nonzero_exit(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check treats empty parsed results as success despite non-zero exit."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("setuptools==80.9.0\n")

    osv_output = (
        "Scanning dir /tmp/example\n"
        '{"results": [], "experimental_config": {"licenses": {"summary": false}}}\n'
    )

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=False, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_no_package_sources_sets_parse_failures_count(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Plain-text no-op scans report parse_failures_count=0 for CI classification."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    osv_output = (
        "Scanned 0 packages and found 0 vulnerabilities\nNo package sources found\n"
    )

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=False, stderr=osv_output, output=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(0)


def test_check_zero_packages_without_no_sources_is_not_success(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Ambiguous zero-package output without the no-sources signal stays a failure."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    osv_output = "Scanned 0 packages and found 0 vulnerabilities\n"

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=False, stderr=osv_output, output=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_garbage_stdout_with_zero_exit_is_not_clean(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Unparseable stdout with a zero exit must not report a clean scan (#1044)."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    garbage = "}{ this is not valid json at all"

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True, stdout=garbage),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_error_payload_without_results_is_not_success(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check does not treat error-only JSON payloads as clean scans."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("setuptools==80.9.0\n")

    osv_output = '{"error": "failed to load config"}\n'

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=False, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_exit_zero_error_payload_fails_closed(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Exit-0 with an error-shaped payload is a scan failure, not a clean pass.

    osv-scanner can exit 0 while emitting ``{"error": ...}``. Reporting that as
    a clean scan would be a silent false-negative in a security gate (#1028).

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("setuptools==80.9.0\n")

    osv_output = '{"error": "failed to load config"}\n'

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_exit_zero_results_not_a_list_fails_closed(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Exit-0 with a non-list ``results`` value fails closed (#1028).

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("setuptools==80.9.0\n")

    osv_output = '{"results": "not-a-list"}\n'

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_exit_zero_empty_results_is_clean(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Exit-0 with an empty results list is a legitimate clean scan (#1028).

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("setuptools==80.9.0\n")

    osv_output = '{"results": []}\n'

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(0)


def test_payload_has_valid_results_discriminates_shapes() -> None:
    """The results-shape guard accepts only dicts with a list ``results``."""
    assert_that(
        OsvScannerPlugin._payload_has_valid_results({"results": []}),
    ).is_true()
    assert_that(
        OsvScannerPlugin._payload_has_valid_results({"results": [{"x": 1}]}),
    ).is_true()
    assert_that(
        OsvScannerPlugin._payload_has_valid_results({"error": "boom"}),
    ).is_false()
    assert_that(
        OsvScannerPlugin._payload_has_valid_results({"results": "nope"}),
    ).is_false()


def test_check_with_vulnerabilities(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when vulnerabilities found.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.25.0\n")

    osv_output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": str(lockfile)},
                    "packages": [
                        {
                            "package": {
                                "name": "requests",
                                "version": "2.25.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-9wx4-h78v-vm56"],
                                    "max_severity": "HIGH",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-9wx4-h78v-vm56",
                                    "summary": "Session verify bypass",
                                    "affected": [
                                        {
                                            "package": {
                                                "name": "requests",
                                                "ecosystem": "PyPI",
                                            },
                                            "ranges": [
                                                {
                                                    "type": "ECOSYSTEM",
                                                    "events": [
                                                        {"introduced": "0"},
                                                        {"fixed": "2.32.0"},
                                                    ],
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    )

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=False, stdout=osv_output),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issues = cast(list[OsvScannerIssue], result.issues)
    assert_that(issues[0].vuln_id).is_equal_to("GHSA-9wx4-h78v-vm56")
    assert_that(issues[0].package_name).is_equal_to("requests")
    assert_that(issues[0].severity).is_equal_to("HIGH")
    assert_that(issues[0].fixed_version).is_equal_to("2.32.0")


def test_check_timeout(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.25.0\n")

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(
            cmd=["osv-scanner"],
            timeout=120,
        ),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_empty_paths(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """Check returns early when given no paths.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
    """
    result = osv_scanner_plugin.check([], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# =============================================================================
# Tests for fix method
# =============================================================================


def test_fix_raises_not_implemented(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Fix method raises NotImplementedError.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        osv_scanner_plugin.fix(["src/"], {})


# =============================================================================
# Tests for OsvScannerIssue DEFAULT_SEVERITY
# =============================================================================


def test_issue_default_severity_is_error() -> None:
    """OsvScannerIssue falls back to ERROR severity when severity is empty."""
    issue = OsvScannerIssue(
        vuln_id="GHSA-test-1234",
        severity="",
        package_name="foo",
        package_version="1.0.0",
    )
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


# =============================================================================
# Tests for suppression staleness detection
# =============================================================================


def test_check_with_suppressions_detects_stale(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check classifies suppressions when .osv-scanner.toml exists."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    # Create a config with one suppression
    config = tmp_path / ".osv-scanner.toml"
    config.write_text(
        "[[IgnoredVulns]]\n"
        'id = "GHSA-stale-1234"\n'
        "ignoreUntil = 2027-12-31\n"
        'reason = "Test suppression"\n',
    )

    # Gating scan: no issues (vuln is suppressed)
    # Probe scan: also no issues (vuln was fixed upstream → stale)
    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(success=True),  # gating scan
            _proc(success=True),  # probe scan
        ],
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.ai_metadata).is_not_none()
    assert result.ai_metadata is not None  # narrow type for mypy
    suppressions = result.ai_metadata["suppressions"]
    assert_that(suppressions).is_length(1)
    assert_that(suppressions[0]["id"]).is_equal_to("GHSA-stale-1234")
    assert_that(suppressions[0]["status"]).is_equal_to("stale")


def test_check_without_config_no_metadata(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check returns no ai_metadata when no .osv-scanner.toml exists."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.ai_metadata).is_none()


def test_check_suppressions_disabled(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """No probe scan when check_suppressions is False."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    config = tmp_path / ".osv-scanner.toml"
    config.write_text(
        "[[IgnoredVulns]]\n"
        'id = "GHSA-1111-aaaa-bbbb"\n'
        "ignoreUntil = 2027-12-31\n"
        'reason = "Test"\n',
    )

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        return_value=_proc(success=True),
    ) as mock_run:
        result = osv_scanner_plugin.check(
            [str(lockfile)],
            {"check_suppressions": False},
        )

    # Only one subprocess call (gating scan, no probe)
    assert_that(mock_run.call_count).is_equal_to(1)
    assert_that(result.ai_metadata).is_none()


def test_check_suppressions_probe_timeout(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Graceful fallback when probe scan times out."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    config = tmp_path / ".osv-scanner.toml"
    config.write_text(
        "[[IgnoredVulns]]\n"
        'id = "GHSA-1111-aaaa-bbbb"\n'
        "ignoreUntil = 2027-12-31\n"
        'reason = "Test"\n',
    )

    with patch.object(
        osv_scanner_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(success=True),  # gating scan succeeds
            subprocess.TimeoutExpired(cmd=["osv-scanner"], timeout=120),  # probe
        ],
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.ai_metadata).is_none()


def test_build_probe_command_internal(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Probe command includes --recursive and --config with null device.

    Tests the private _build_probe_command directly because exercising it
    through check() requires complex subprocess mocking with two sequential
    calls (gating scan + probe scan) that obscures the command structure
    being verified.
    """
    cmd = osv_scanner_plugin._build_probe_command(tmp_path)

    assert_that(cmd).contains("--recursive")
    assert_that(cmd).contains("--config")
    assert_that(cmd).contains(os.devnull)
    assert_that(cmd).contains(str(tmp_path))


def test_find_config_file_in_scan_root(tmp_path: Path) -> None:
    """Finds .osv-scanner.toml in the scan root."""
    config = tmp_path / ".osv-scanner.toml"
    config.write_text("")

    result = OsvScannerPlugin._find_config_file(tmp_path)
    assert_that(result).is_equal_to(config)


def test_find_config_file_in_parent(tmp_path: Path) -> None:
    """Finds .osv-scanner.toml in a parent directory."""
    config = tmp_path / ".osv-scanner.toml"
    config.write_text("")

    child = tmp_path / "frontend"
    child.mkdir()

    result = OsvScannerPlugin._find_config_file(child)
    assert_that(result).is_equal_to(config)


def test_find_config_file_not_found(tmp_path: Path) -> None:
    """Returns None when no .osv-scanner.toml exists."""
    result = OsvScannerPlugin._find_config_file(tmp_path)
    assert_that(result).is_none()


def test_set_options_validates_check_suppressions(
    osv_scanner_plugin: OsvScannerPlugin,
) -> None:
    """set_options rejects non-boolean check_suppressions."""
    with pytest.raises(ValueError, match="check_suppressions must be a boolean"):
        osv_scanner_plugin.set_options(check_suppressions="yes")
