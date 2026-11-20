"""lintro refuses osv-scanner releases that under-read lockfiles (#3557).

Two ranges under-read silently, exiting 0 with nothing on standard error, so
the scan reads as complete:

- 2.4.0 to 2.5.1 read only the first document of a two-document
  ``pnpm-lock.yaml`` (15 of 651 packages in vuejs/core).
- 2.7.0 treats npm packages with no ``resolved`` field in
  ``package-lock.json`` as local and never checks them.

lintro cannot detect either, so the floor is the version lintro pins and
tests (#3556), which is above both ranges. The manifest gives osv_scanner no
``min_version`` of its own, so the floor is read from the pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro.tools
from lintro._tool_versions import TOOL_VERSIONS
from lintro.enums.tool_name import ToolName
from lintro.tools.core.version_parsing import check_tool_version
from lintro.tools.osv_scanner.definition import OsvScannerPlugin

#: The osv-scanner version lintro pins, installs and tests.
_PIN = TOOL_VERSIONS[ToolName.OSV_SCANNER]


def _version_output(version: str) -> str:
    """Build ``osv-scanner --version`` output in the shape real releases print.

    Args:
        version: Release version the fake binary reports.

    Returns:
        The four-line banner current releases print.
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


def test_floor_is_the_pinned_version() -> None:
    """The floor is the pin itself, with no separate value to fall behind.

    The manifest source sets no ``min_version`` for osv_scanner, so the floor
    falls back to the pinned ``version`` and a Renovate bump of the pin
    raises it too.
    """
    source = Path(lintro.tools.__file__).parent / "manifest.src.json"
    tools = json.loads(source.read_text(encoding="utf-8"))["tools"]
    entry = next(tool for tool in tools if tool["name"] == "osv_scanner")

    assert_that(entry).does_not_contain_key("min_version")
    assert_that(OsvScannerPlugin().definition.min_version).is_equal_to(_PIN)


@pytest.mark.parametrize("version", ["2.4.0", "2.5.1", "2.6.0", "2.7.0"])
def test_release_that_under_reads_lockfiles_is_below_minimum(
    tmp_path: Path,
    version: str,
) -> None:
    """Releases with a known silent under-read fail the version check.

    2.6.0 reads pnpm lockfiles correctly but sits below the pin, so it is
    refused too.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: An osv-scanner release below the floor.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_false()
    assert_that(info.error_message).is_equal_to(
        f"Version {version} is below minimum requirement {_PIN}",
    )
    assert_that(info.install_hint).contains(f"v{_PIN}+")


def test_pinned_release_passes(tmp_path: Path) -> None:
    """The pinned release passes the version check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    binary = _fake_osv_scanner(tmp_path, _PIN)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(_PIN)
    assert_that(info.version_check_passed).is_true()
    assert_that(info.error_message).is_none()
