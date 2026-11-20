"""lintro refuses osv-scanner releases that under-read pnpm lockfiles (#3557).

osv-scanner 2.4.0 to 2.5.1 read only the first document of a two-document
``pnpm-lock.yaml`` (15 of 651 packages in vuejs/core) and exit 0 with nothing
on standard error, so the scan reads as complete. lintro cannot detect that,
so the floor sits at 2.6.0, the first release that reads the whole file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro.tools
from lintro.tools.core.version_parsing import check_tool_version
from lintro.tools.osv_scanner.definition import OsvScannerPlugin

#: The floor this module pins.
_FLOOR = "2.6.0"


def _version_output(version: str) -> str:
    """Build ``osv-scanner --version`` output in the shape real releases print.

    Args:
        version: Release version the fake binary reports.

    Returns:
        The four-line banner 2.5.1 and 2.6.0 print.
    """
    return (
        f"osv-scanner version: {version}\n"
        "osv-scalibr version: 0.5.2\n"
        "commit: 0000000000000000000000000000000000000000\n"
        "built at: 2026-09-14T01:44:58Z\n"
    )


def _fake_osv_scanner(directory: Path, version: str) -> Path:
    """Write an executable that answers ``--version`` like osv-scanner.

    Args:
        directory: Directory to create the executable in.
        version: Release version the executable reports.

    Returns:
        Path to the executable.
    """
    binary = directory / "osv-scanner"
    binary.write_text(
        f"#!/bin/sh\ncat <<'EOF'\n{_version_output(version)}EOF\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def test_manifest_floor_is_2_6_0() -> None:
    """The manifest source and the plugin both declare 2.6.0 as the floor."""
    source = Path(lintro.tools.__file__).parent / "manifest.src.json"
    tools = json.loads(source.read_text(encoding="utf-8"))["tools"]
    entry = next(tool for tool in tools if tool["name"] == "osv_scanner")

    assert_that(entry["min_version"]).is_equal_to(_FLOOR)
    assert_that(OsvScannerPlugin().definition.min_version).is_equal_to(_FLOOR)


@pytest.mark.parametrize("version", ["2.4.0", "2.5.1"])
def test_release_that_under_reads_pnpm_is_below_minimum(
    tmp_path: Path,
    version: str,
) -> None:
    """2.4.0 to 2.5.1 fail the version check with an actionable message.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: An osv-scanner release below the floor.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_false()
    assert_that(info.error_message).is_equal_to(
        f"Version {version} is below minimum requirement {_FLOOR}",
    )
    assert_that(info.install_hint).contains(f"v{_FLOOR}+")


@pytest.mark.parametrize("version", ["2.6.0", "2.7.1"])
def test_release_that_reads_whole_pnpm_lockfile_passes(
    tmp_path: Path,
    version: str,
) -> None:
    """2.6.0 and later pass the version check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: An osv-scanner release at or above the floor.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_true()
    assert_that(info.error_message).is_none()
