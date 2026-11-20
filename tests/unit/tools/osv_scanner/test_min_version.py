"""lintro requires the osv-scanner version it pins (#3557).

Older releases under-read lockfiles silently, exiting 0 with nothing on
standard error, so the scan reads as complete:

- 2.4.0 to 2.5.1 read only the first document of a two-document
  ``pnpm-lock.yaml`` (15 of 651 packages in vuejs/core).
- 2.7.0 treats npm packages with no ``resolved`` field in  # meta-guard: allow
  ``package-lock.json`` as local and never checks them (fixed in 2.7.1).

lintro cannot detect either, so the minimum is the version lintro pins and
tests (#3556). The manifest gives osv_scanner no ``min_version`` of its own,
so the minimum is read from the pin and moves with it. The tests below assert
that rule and derive every version they try from the pin, so they keep
holding when Renovate moves it.
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

#: The last release that reads only the first document of a two-document
#: ``pnpm-lock.yaml``; it must stay refused whatever the pin is.
_LAST_PNPM_UNDER_READ = "2.5.1"


def _patch_offset(version: str, offset: int) -> str:
    """Return ``version`` with its patch number moved by ``offset``.

    Args:
        version: A ``major.minor.patch`` release version.
        offset: Amount to add to the patch number.

    Returns:
        The shifted version.
    """
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + offset}"


def _below_pin() -> list[str]:
    """Versions below the pin: a fixed old release and the previous patch.

    Returns:
        Release versions strictly below the pin.
    """
    below = [_LAST_PNPM_UNDER_READ]
    if int(_PIN.split(".")[2]) > 0:
        below.append(_patch_offset(_PIN, -1))
    return below


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


def test_minimum_is_the_pinned_version() -> None:
    """The minimum is the pin itself, with no separate value to fall behind.

    The manifest source sets no ``min_version`` for osv_scanner, so the
    minimum falls back to the pinned ``version`` and a bump of the pin
    raises it too.
    """
    source = Path(lintro.tools.__file__).parent / "manifest.src.json"
    tools = json.loads(source.read_text(encoding="utf-8"))["tools"]
    entry = next(tool for tool in tools if tool["name"] == "osv_scanner")

    assert_that(entry).does_not_contain_key("min_version")
    assert_that(OsvScannerPlugin().definition.min_version).is_equal_to(_PIN)


def test_pin_is_above_the_pnpm_under_read() -> None:
    """The pin never falls back into the range that under-reads pnpm lockfiles."""
    pin = tuple(int(part) for part in _PIN.split("."))
    last_bad = tuple(int(part) for part in _LAST_PNPM_UNDER_READ.split("."))

    assert_that(pin > last_bad).is_true()


@pytest.mark.parametrize("version", _below_pin())
def test_release_below_the_pin_is_below_minimum(
    tmp_path: Path,
    version: str,
) -> None:
    """A release below the pin fails the version check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: An osv-scanner release below the pin.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_false()
    assert_that(info.error_message).is_equal_to(
        f"Version {version} is below minimum requirement {_PIN}",
    )
    assert_that(info.install_hint).contains(f"v{_PIN}+")


@pytest.mark.parametrize(
    "version",
    [_PIN, _patch_offset(_PIN, 1)],
    ids=["pin", "above-pin"],
)
def test_pinned_or_newer_release_passes(tmp_path: Path, version: str) -> None:
    """The pinned release and a newer one pass the version check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: The pin, or a release above it.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_true()
    assert_that(info.error_message).is_none()
