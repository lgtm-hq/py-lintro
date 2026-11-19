"""Real osv-scanner runs that did not scan everything (#3352).

A lockfile osv-scanner cannot read or parse, and a scan whose vulnerability
lookups fail, must never read as "no files" or as a clean pass.
"""

from __future__ import annotations

import os
import shutil
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool
from tests.integration.tools.osv_scanner.conftest import VIOLATION_SAMPLE

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("osv-scanner")

_PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def _assert_incomplete(result: ToolResult) -> None:
    """Assert a result is an incomplete run, not a scan verdict.

    Args:
        result: The plugin's result.
    """
    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()


@pytest.fixture
def unreadable_lockfile(tmp_path: Path) -> Iterator[Path]:
    """Provide a requirements.txt nobody may read.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Yields:
        Path: The lockfile, readable again after the test so it can be removed.
    """
    lockfile = tmp_path / "requirements.txt"
    shutil.copy(VIOLATION_SAMPLE, lockfile)
    lockfile.chmod(0)
    try:
        if os.access(lockfile, os.R_OK):
            pytest.skip("file permissions do not apply to this user (root)")
        yield lockfile
    finally:
        lockfile.chmod(0o600)


@pytest.fixture
def dead_proxy(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point every proxy variable at a local port nothing listens on.

    Args:
        monkeypatch: Pytest fixture for environment changes.

    Returns:
        The proxy URL; connections to it are refused at once.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    for name in _PROXY_VARIABLES:
        monkeypatch.setenv(name, url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    return url


def test_unreadable_lockfile_is_an_incomplete_run(
    get_plugin: Callable[[str], BaseToolPlugin],
    unreadable_lockfile: Path,
) -> None:
    """A lockfile osv-scanner cannot open is not "no files" (#3352).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        unreadable_lockfile: A requirements.txt with mode 000.
    """
    result = get_plugin("osv_scanner").check([str(unreadable_lockfile.parent)], {})

    _assert_incomplete(result)
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains("requirements.txt").contains(
        "permission denied",
    )


def test_malformed_lockfile_is_an_incomplete_run(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A lockfile osv-scanner cannot parse is not "no files" (#3352).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "package-lock.json").write_text("not json{\n")

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains("package-lock.json").contains(
        "invalid character",
    )


def test_broken_lockfile_beside_a_vulnerable_one_keeps_its_findings(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Findings from the lockfile read survive; the run is still incomplete.

    Queries the public OSV API, as the other vulnerability tests here do.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    shutil.copy(VIOLATION_SAMPLE, tmp_path / "requirements.txt")
    (tmp_path / "package-lock.json").write_text("garbage{\n")

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.output).contains("package-lock.json").contains(
        "invalid character",
    )


def test_failed_vulnerability_lookup_is_not_a_clean_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    dead_proxy: str,
) -> None:
    """Lookups that never reach the database prove nothing is clean (#3352).

    osv-scanner 2.7.1 exits 127 with ``{"results": []}`` here; 2.4.0 prints no
    report. Neither may pass, and the vulnerable pin must not be hidden as a
    clean, baseline-eligible scan. Nothing leaves the machine: the proxy is a
    closed local port.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        dead_proxy: Proxy URL on a closed local port.
    """
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.output).contains("connection").does_not_contain(
        str(tmp_path).lstrip(os.sep),
    )


#: A package name no public registry has, as an internal package would be.
_PRIVATE_PACKAGE = "zz-private-internal-pkg-xq"


def test_private_package_beside_a_vulnerable_one_is_a_complete_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A package missing from public PyPI leaves the scan complete (#3352).

    osv-scanner resolves the indirect dependencies of a requirements.txt and
    logs an extraction error for the package it cannot find there, but the
    listed packages are still scanned. Queries the public OSV and package
    APIs, as the other vulnerability tests here do.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "requirements.txt").write_text(
        f"flask==3.0.0\n{_PRIVATE_PACKAGE}==1.2.3\n",
    )

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.output).starts_with("Note: OSV-Scanner scanned").contains(
        _PRIVATE_PACKAGE,
    )


def test_private_package_alone_is_a_clean_complete_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """osv-scanner's exit 127 for the failed resolution is not a failure.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "requirements.txt").write_text(
        f"six==1.16.0\n{_PRIVATE_PACKAGE}==1.2.3\n",
    )

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).contains("requirements.txt").contains(
        _PRIVATE_PACKAGE,
    )


def test_private_package_with_the_network_down_is_incomplete(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    dead_proxy: str,
) -> None:
    """A failed resolution beside failed lookups stays an incomplete run.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        dead_proxy: Proxy URL on a closed local port.
    """
    (tmp_path / "requirements.txt").write_text(
        f"six==1.16.0\n{_PRIVATE_PACKAGE}==1.2.3\n",
    )

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)


def test_failed_resolution_under_an_excluded_path_is_a_clean_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """The exclusion reads the manifest out of ``failed resolution for``.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "requirements.txt").write_text(
        f"{_PRIVATE_PACKAGE}==1.2.3\n",
    )
    plugin = get_plugin("osv_scanner")
    plugin.set_options(exclude_patterns=["vendor"])

    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.output).is_none()


def test_several_malformed_lockfiles_are_an_incomplete_run(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """One extractor failing on two files is not "no files" (#3352).

    osv-scanner then prints the files on tab-indented lines of their own.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "package-lock.json").write_text("not json{\n")

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.output).contains("a/package-lock.json").contains(
        "b/package-lock.json",
    )


def test_excluded_directory_does_not_hide_a_sibling_with_a_colon(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """``vendor: data/package-lock.json`` is not under an excluded ``vendor/``.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor: data").mkdir()
    (tmp_path / "vendor: data" / "package-lock.json").write_text("not json{\n")
    plugin = get_plugin("osv_scanner")
    plugin.set_options(exclude_patterns=["vendor"])

    result = plugin.check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.output).contains("vendor: data/package-lock.json")
