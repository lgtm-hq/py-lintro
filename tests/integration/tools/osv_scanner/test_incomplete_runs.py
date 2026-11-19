"""Real osv-scanner runs: which are complete scans and which are not (#3352).

lintro runs osv-scanner at ``--verbosity error``. A lockfile osv-scanner
cannot read or parse, a broken configuration, and a scan whose lookups fail
must never read as "no files" or as a clean pass; a private package, a
configuration with ignores, and a Go module scanned without Go installed are
complete scans.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.utils.tool_metadata import tool_notes
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


@pytest.fixture
def dead_resolver(dead_proxy: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Leave only the OSV API reachable, so dependency resolution fails.

    Vulnerability lookups go straight to the OSV API, as in the other tests
    here; dependency resolution (deps.dev over gRPC) goes to the closed
    local proxy port, so it reaches nothing.

    Args:
        dead_proxy: Proxy URL on a closed local port.
        monkeypatch: Pytest fixture for environment changes.

    Returns:
        The proxy URL.
    """
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "api.osv.dev,osv.dev")
    return dead_proxy


def _note(result: ToolResult) -> str:
    """Return the one note a result carries.

    Args:
        result: The plugin's result.

    Returns:
        The note's message.
    """
    notes = tool_notes(result.metadata)
    assert_that(notes).is_length(1)
    return notes[0]["message"]


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
    assert_that(result.output).contains("connection refused")


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
    assert_that(_note(result)).starts_with("OSV-Scanner scanned").contains(
        _PRIVATE_PACKAGE,
    )
    # The note is kept apart from the report, which still parses.
    assert_that(json.loads(result.output or "")).contains_key("results")


#: Ignores of the kinds real projects keep in their ``osv-scanner.toml``: a
#: filtered vulnerability with a reason over two lines, an ignore that matches
#: nothing, a duplicate, and package overrides, one of them unused.
_CONFIG = '''
[[IgnoredVulns]]
id = "PYSEC-2026-2151"
ignoreUntil = 2099-01-01
reason = """accepted for this test
second line of the reason"""

[[IgnoredVulns]]
id = "GHSA-zzzz-zzzz-zzzz"
reason = "never matches"

[[IgnoredVulns]]
id = "PYSEC-2026-2151"

[[PackageOverrides]]
name = "six"
ecosystem = "PyPI"
ignore = true
reason = "vendored copy"

[[PackageOverrides]]
name = "zz-nothere"
ecosystem = "PyPI"
ignore = true
'''


@pytest.mark.parametrize(
    "name",
    ["osv-scanner.toml", ".osv-scanner.toml"],
    ids=["found-by-osv-scanner", "passed-by-lintro"],
)
def test_configuration_with_ignores_is_a_complete_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    name: str,
) -> None:
    """A configuration's own messages are not errors, so the scan is complete.

    ``osv-scanner.toml`` is the file osv-scanner finds itself, as in DOMPurify
    and es-toolkit; lintro passes ``.osv-scanner.toml`` with ``--config``.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        name: The configuration file's name.
    """
    (tmp_path / "requirements.txt").write_text(
        f"{VIOLATION_SAMPLE.read_text()}\nflask==3.0.0\nsix==1.16.0\n",
    )
    (tmp_path / name).write_text(_CONFIG)

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_greater_than(0)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        (".osv-scanner.toml", "Failed to read config file"),
        ("osv-scanner.toml", "Ignored invalid config file at"),
    ],
    ids=["passed-by-lintro", "found-by-osv-scanner"],
)
def test_broken_configuration_is_an_incomplete_run(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    name: str,
    message: str,
) -> None:
    """A configuration osv-scanner cannot read never leaves a verdict.

    Passed with ``--config`` it stops the scan (exit 127); found by
    osv-scanner's own discovery it is ignored and the scan runs without it
    (exit 130), so its ignores are lost.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        name: The configuration file's name.
        message: The start of osv-scanner's error.
    """
    (tmp_path / "requirements.txt").write_text("six==1.16.0\n")
    (tmp_path / name).write_text("this is = = not toml\n")

    result = get_plugin("osv_scanner").check(
        [str(tmp_path)],
        {"check_suppressions": False},
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(message).contains("toml:")


#: A Go module with a known-vulnerable dependency.
_GO_MOD = """module example.com/demo

go 1.21

require golang.org/x/text v0.3.0
"""

_GO_SUM = (
    "golang.org/x/text v0.3.0 h1:g61tztE5qeGQ89tm6NTjjM9VPIm088od1l6aSorWRWg=\n"
    "golang.org/x/text v0.3.0/go.mod h1:NqM8EUOU14njkJ3fqMW+pc6Ldnwhi/IjpwHt7yyuwOQ=\n"
)


def test_go_module_without_go_installed_is_a_complete_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Go on PATH the dependencies are still scanned from go.mod.

    Call analysis, the only step that needs Go, is off.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        monkeypatch: Pytest fixture for environment changes.
    """
    scanner = shutil.which("osv-scanner") or pytest.fail("osv-scanner not on PATH")
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "osv-scanner").symlink_to(scanner)
    path = os.pathsep.join([str(tools), "/usr/bin", "/bin"])
    if shutil.which("go", path=path) is not None:
        pytest.skip("Go is installed in a system directory")
    monkeypatch.setenv("PATH", path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text(_GO_MOD)
    (project / "go.sum").write_text(_GO_SUM)
    (project / "main.go").write_text(
        'package main\n\nimport _ "golang.org/x/text/language"\n\nfunc main() {}\n',
    )

    result = get_plugin("osv_scanner").check([str(project)], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_greater_than(0)


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
    assert_that(result.output).is_none()
    assert_that(_note(result)).contains("requirements.txt").contains(
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


@pytest.mark.parametrize(
    "requirements",
    [f"six==1.16.0\n{_PRIVATE_PACKAGE}==1.2.3\n", "six==1.16.0\n", "flask==3.0.0\n"],
    ids=["private-package", "public-only", "vulnerable"],
)
def test_resolution_service_outage_is_an_incomplete_run(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    dead_resolver: str,
    requirements: str,
) -> None:
    """deps.dev unreachable with OSV lookups working is never a clean scan.

    osv-scanner logs only a failed resolution here and exits 127 (or 1 beside
    a finding); the indirect dependencies were not checked because of an
    outage, not because the registry lacks a package.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        dead_resolver: Proxy URL on a closed local port, OSV API exempt.
        requirements: The requirements.txt content.
    """
    (tmp_path / "requirements.txt").write_text(requirements)

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    _assert_incomplete(result)
    assert_that(result.output).contains("requirements.txt: ").contains(
        "rpc error",
    )
    assert_that(tool_notes(result.metadata)).is_empty()


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


@pytest.mark.parametrize(
    "pins",
    [
        {"a": "flask", "b": "flask"},
        {"a": "flask", "b": "requests", "c": "flask"},
        {"a": "flask", "c": "django", "p": f"six==1.16.0\n{_PRIVATE_PACKAGE}"},
    ],
    ids=["two", "three", "unsatisfiable-and-private"],
)
def test_several_unsatisfiable_manifests_are_a_complete_scan(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    pins: dict[str, str],
) -> None:
    """The blank line osv-scanner prints between their errors is read.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        pins: Package per manifest directory; each is pinned to a version
            that does not exist.
    """
    for name, package in pins.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "requirements.txt").write_text(f"{package}==99999.0.0\n")

    result = get_plugin("osv_scanner").check([str(tmp_path)], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(_note(result)).contains("resolution impossible")
