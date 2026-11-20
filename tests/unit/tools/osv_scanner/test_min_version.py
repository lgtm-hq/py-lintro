"""lintro refuses osv-scanner releases below 2.6.0 (#3557).

The minimum is a deliberate number set on evidence, not the pinned version
(#3556): it is the oldest release lintro supports that reads lockfiles
correctly.

- osv-scanner 2.4.0 to 2.5.1 read only the first document of a two-document
  ``pnpm-lock.yaml``: 15 of 651 packages in vuejs/core ``942d307``. 2.5.1
  reports 0 vulnerabilities and exits 0 with nothing on standard error, so the
  scan reads as complete and clean and lintro cannot detect the under-read.
- osv-scanner 2.6.0 reads all 651 packages and reports 34 vulnerabilities,
  and read every lockfile format tried in review correctly.

A known-bad release above the minimum (2.7.0 skips npm packages with no  # meta-guard: allow
``resolved`` field, fixed in 2.7.1) is not refused here; a per-tool list of
such versions is separate work in #3556.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import TOOL_VERSIONS
from lintro.enums.tool_name import ToolName
from lintro.tools.core.version_parsing import check_tool_version
from lintro.tools.osv_scanner.definition import OsvScannerPlugin

#: The oldest osv-scanner release that reads a two-document pnpm lockfile.
_MINIMUM = "2.6.0"

#: The osv-scanner version lintro pins, installs and tests.
_PIN = TOOL_VERSIONS[ToolName.OSV_SCANNER]


def _next_minor(version: str) -> str:
    """Return the first release of the minor series after ``version``.

    Args:
        version: A ``major.minor.patch`` release version.

    Returns:
        ``major.(minor + 1).0``.
    """
    major, minor, _patch = (int(part) for part in version.split("."))
    return f"{major}.{minor + 1}.0"


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


def test_minimum_is_the_oldest_correct_release() -> None:
    """The plugin's minimum is 2.6.0, the first release without the pnpm gap."""
    assert_that(OsvScannerPlugin().definition.min_version).is_equal_to(_MINIMUM)


@pytest.mark.parametrize("version", ["2.4.0", "2.5.1"])
def test_release_that_under_reads_pnpm_is_refused(
    tmp_path: Path,
    version: str,
) -> None:
    """A release that reads one document of a pnpm lockfile fails the check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: An osv-scanner release from the under-reading range.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_false()
    assert_that(info.error_message).is_equal_to(
        f"Version {version} is below minimum requirement {_MINIMUM}",
    )
    assert_that(info.install_hint).contains(f"v{_MINIMUM}+")


@pytest.mark.parametrize(
    "version",
    [_MINIMUM, _PIN, _next_minor(_PIN)],
    ids=["minimum", "pin", "above-pin"],
)
def test_supported_release_passes(tmp_path: Path, version: str) -> None:
    """The minimum, the pinned release and a newer one pass the check.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        version: A release at or above the minimum.
    """
    binary = _fake_osv_scanner(tmp_path, version)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.current_version).is_equal_to(version)
    assert_that(info.version_check_passed).is_true()
    assert_that(info.error_message).is_none()
