"""Unit tests for OSV-Scanner plugin."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.enums.tool_type import ToolType
from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue
from lintro.tools.definitions.osv_scanner import (
    OSV_SCANNER_DEFAULT_TIMEOUT,
    OsvScannerPlugin,
    _find_scan_root,
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
        "_run_subprocess",
        return_value=(True, ""),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


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
        "_run_subprocess",
        return_value=(False, osv_output),
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
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(
            cmd=["osv-scanner"],
            timeout=120,
        ),
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_no_lockfiles_skips(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully when no lockfiles are found.

    Args:
        osv_scanner_plugin: The OsvScannerPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a directory with no lockfiles
    src_file = tmp_path / "main.py"
    src_file.write_text("print('hello')\n")

    result = osv_scanner_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()


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
# Tests for _find_scan_root
# =============================================================================


def test_find_scan_root_empty_list() -> None:
    """Empty path list returns None."""
    assert_that(_find_scan_root([])).is_none()


def test_find_scan_root_single_file(tmp_path: Path) -> None:
    """Single file returns its parent directory."""
    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("requests==2.32.3\n")

    result = _find_scan_root([str(lockfile)])
    assert_that(result).is_equal_to(tmp_path)


def test_find_scan_root_single_directory(tmp_path: Path) -> None:
    """Single directory returns itself."""
    result = _find_scan_root([str(tmp_path)])
    assert_that(result).is_equal_to(tmp_path)


def test_find_scan_root_same_directory(tmp_path: Path) -> None:
    """Multiple files in same directory returns that directory."""
    req = tmp_path / "requirements.txt"
    req.write_text("")
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}")

    result = _find_scan_root([str(req), str(lock)])
    assert_that(result).is_equal_to(tmp_path)


def test_find_scan_root_nested_directories(tmp_path: Path) -> None:
    """Multiple files in nested directories returns common ancestor."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    backend = tmp_path / "backend"
    backend.mkdir()

    pkg_lock = frontend / "package-lock.json"
    pkg_lock.write_text("{}")
    req = backend / "requirements.txt"
    req.write_text("")

    result = _find_scan_root([str(pkg_lock), str(req)])
    assert_that(result).is_equal_to(tmp_path)


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
        "_run_subprocess",
        side_effect=[
            (True, ""),  # gating scan
            (True, ""),  # probe scan
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
        "_run_subprocess",
        return_value=(True, ""),
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
        "_run_subprocess",
        return_value=(True, ""),
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
        "_run_subprocess",
        side_effect=[
            (True, ""),  # gating scan succeeds
            subprocess.TimeoutExpired(cmd=["osv-scanner"], timeout=120),  # probe
        ],
    ):
        result = osv_scanner_plugin.check([str(lockfile)], {})

    assert_that(result.success).is_true()
    assert_that(result.ai_metadata).is_none()


def test_build_probe_command_internal(osv_scanner_plugin: OsvScannerPlugin) -> None:
    """Probe command includes --config with platform null device.

    Tests the private _build_probe_command directly because exercising it
    through check() requires complex subprocess mocking with two sequential
    calls (gating scan + probe scan) that obscures the command structure
    being verified.
    """
    cmd = osv_scanner_plugin._build_probe_command(["requirements.txt"])

    assert_that(cmd).contains("--config")
    assert_that(cmd).contains(os.devnull)
    assert_that(cmd).contains("--lockfile")
    assert_that(cmd).contains("requirements.txt")


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
