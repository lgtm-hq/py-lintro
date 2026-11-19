"""How OSV-Scanner's ``Error during extraction`` lines are read (#3352).

Every standard error below is what osv-scanner 2.4.0 or 2.7.1 really printed
for the case, with the scanned directory written as ``{root}`` (relative to
``/``, as osv-scanner prints it).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.utils.meaningful_run import baseline_is_eligible

_PRIVATE = "zz-private-internal-pkg-xq"

_HEADER = "Scanning dir .\nStarting filesystem walk for root: /\n"

_END = "End status: 1 dirs visited, 2 inodes visited, 1 Extract calls\n"

#: A requirements.txt naming a package public PyPI does not have, healthy
#: network. 2.4.0 and 2.7.1 print the same lines.
_PRIVATE_PACKAGE_STDERR = (
    _HEADER
    + "Scanned /{root}/requirements.txt file and found 2 packages\n"
    + _END
    + "failed resolution for {root}/requirements.txt: package "
    f"System(7):{_PRIVATE}: not found\n"
    "Error during extraction: (extracting as transitivedependency/requirements) "
    f"failed resolution for {{root}}/requirements.txt: package System(7):{_PRIVATE}: "
    "not found\n"
)

#: Two manifests with private packages; 2.7.1 order, joined by a newline.
_TWO_PRIVATE_STDERR_2_7 = (
    _HEADER + "Scanned /{root}/a/requirements.txt file and found 1 package\n"
    "Scanned /{root}/b/requirements.txt file and found 1 package\n"
    + _END
    + "failed resolution for {root}/a/requirements.txt: package "
    "System(7):zz-a: not found\n"
    "failed resolution for {root}/b/requirements.txt: package "
    "System(7):zz-b: not found\n"
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/a/requirements.txt: package System(7):zz-a: "
    "not found\n"
    "failed resolution for {root}/b/requirements.txt: package System(7):zz-b: "
    "not found\n"
)

#: The same in 2.4.0, which resolves the manifests in the other order.
_TWO_PRIVATE_STDERR_2_4 = (
    _HEADER + _END + "failed resolution for {root}/b/requirements.txt: package "
    "System(7):zz-b: not found\n"
    "failed resolution for {root}/a/requirements.txt: package "
    "System(7):zz-a: not found\n"
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/b/requirements.txt: package System(7):zz-b: "
    "not found\n"
    "failed resolution for {root}/a/requirements.txt: package System(7):zz-a: "
    "not found\n"
)

#: A resolution error whose own message runs over two lines (2.4.0 and 2.7.1).
_MULTILINE_RESOLUTION_STDERR = (
    _HEADER
    + _END
    + "failed resolution for {root}/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "====="\n'
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "====="\n'
    "\n"
    "Filtered 2 local/unscannable package/s from the scan.\n"
)

#: A pom.xml depending on an artifact no registry has (2.4.0 and 2.7.1).
_MAVEN_STDERR = (
    _HEADER
    + "Scanned /{root}/pom.xml file and found 1 package\n"
    + _END
    + "Warning: enricher transitivedependency/pomxml may be risky when run on "
    "untrusted artifacts. Please ensure you trust the source code and "
    "artifacts.\n"
    "failed resolution for {root}/pom.xml: failed resolving "
    "{{Maven:x:y[Concrete:1] {{}}}}: version Maven:zz.private:nothere"
    "[Concrete:1.2.3]: not found\n"
    "Error during extraction: (extracting as transitivedependency/pomxml) "
    "failed resolution for {root}/pom.xml: failed resolving "
    "{{Maven:x:y[Concrete:1] {{}}}}: version Maven:zz.private:nothere"
    "[Concrete:1.2.3]: not found\n"
)

_CONNECTION_REFUSED = (
    'connection error: desc = "transport: Error while dialing: dial tcp '
    '127.0.0.1:9: connect: connection refused"'
)

#: The private-package manifest with the network down, osv-scanner 2.7.1.
_DEAD_NETWORK_STDERR_2_7 = (
    _HEADER
    + _END
    + "failed resolution for {root}/requirements.txt: max retries exhausted: "
    f"failed after 4 attempts: rpc error: code = Unavailable desc = "
    f"{_CONNECTION_REFUSED}\n"
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/requirements.txt: max retries exhausted: "
    f"failed after 4 attempts: rpc error: code = Unavailable desc = "
    f"{_CONNECTION_REFUSED}\n"
    "Error during extraction: (extracting as vulnmatch/osvdev) max retries "
    'exceeded: attempt 4: request failed: Post "https://api.osv.dev/v1/querybatch":'
    " proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection refused\n"
)

#: The same in osv-scanner 2.4.0, which then prints no report.
_DEAD_NETWORK_STDERR_2_4 = (
    _HEADER + _END + "failed resolution for {root}/requirements.txt: rpc error: code = "
    f"Unavailable desc = {_CONNECTION_REFUSED}\n"
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/requirements.txt: rpc error: code = "
    f"Unavailable desc = {_CONNECTION_REFUSED}\n"
    "error when retrieving vulns: max retries exceeded: attempt 4: request "
    'failed: Post "https://api.osv.dev/v1/querybatch": proxyconnect tcp: dial '
    "tcp 127.0.0.1:9: connect: connection refused\n"
)

#: Two malformed lockfiles read by one extractor (2.4.0 and 2.7.1).
_TWO_MALFORMED_STDERR = (
    _HEADER
    + _END
    + "Error during extraction: (extracting as javascript/packagelockjson) \n"
    "\t{root}/a/package-lock.json: could not extract: invalid character 'o' "
    "in literal null (expecting 'u')\n"
    "\t{root}/b/package-lock.json: could not extract: invalid character 'g' "
    "looking for beginning of value\n"
    "No package sources found, --help for usage information.\n"
)

_EMPTY_REPORT = '{"results": [], "experimental_config": {}}'


def _malformed(name: str) -> str:
    """Return the line osv-scanner prints for one malformed package-lock.json.

    Args:
        name: The lockfile's path under the scanned directory.

    Returns:
        The line, with ``{root}`` for the scanned directory.
    """
    return (
        "Error during extraction: (extracting as javascript/packagelockjson) "
        f"{{root}}/{name}: could not extract: invalid character 'o' in literal "
        "null (expecting 'u')\n"
    )


def _flask_report(source: Path) -> str:
    """Return an osv-scanner report naming one vulnerable flask pin.

    Args:
        source: The manifest the finding is reported for.

    Returns:
        The JSON report.
    """
    package = {"name": "flask", "version": "3.0.0", "ecosystem": "PyPI"}
    return json.dumps(
        {
            "results": [
                {
                    "source": {"path": str(source), "type": "lockfile"},
                    "packages": [
                        {
                            "package": package,
                            "vulnerabilities": [{"id": "GHSA-68rp-wp8r-4726"}],
                            "groups": [{"ids": ["GHSA-68rp-wp8r-4726"]}],
                        },
                    ],
                },
            ],
        },
    )


def _check(
    plugin: OsvScannerPlugin,
    scanned: Path,
    *,
    files: dict[str, str],
    returncode: int,
    stderr: str,
    stdout: str = _EMPTY_REPORT,
    printed_root: Path | None = None,
) -> ToolResult:
    """Run the plugin's check against one recorded osv-scanner process.

    Args:
        plugin: Plugin under test.
        scanned: Directory handed to the plugin.
        files: Files to create under ``scanned``; a name ending in ``/``
            creates a directory.
        returncode: Exit status of osv-scanner.
        stderr: Its standard error, with ``{root}`` for the scanned directory.
        stdout: Its standard output.
        printed_root: The directory as osv-scanner prints it; ``scanned``
            when omitted.

    Returns:
        The plugin's result.
    """
    for name, content in files.items():
        target = scanned / name
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    root = str(printed_root or scanned).lstrip("/")
    text = stderr.format(root=root)
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=text,
        output=stdout + text,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(scanned)], {"check_suppressions": False})


def _assert_complete(result: ToolResult) -> None:
    """Assert a result is a complete scan verdict a baseline may use.

    Args:
        result: The plugin's result.
    """
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.no_files).is_false()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_true()


def _assert_incomplete(result: ToolResult) -> None:
    """Assert a result is an incomplete run, never a verdict.

    Args:
        result: The plugin's result.
    """
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.no_files).is_false()


def test_private_package_beside_a_vulnerable_one_is_a_complete_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """A package missing from public PyPI does not make the scan incomplete."""
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={"requirements.txt": f"flask==3.0.0\n{_PRIVATE}==1.2.3\n"},
        returncode=1,
        stdout=_flask_report(tmp_path / "requirements.txt"),
        stderr=_PRIVATE_PACKAGE_STDERR,
    )

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_false()
    assert_that(result.issues).extracting("vuln_id").is_equal_to(
        ["GHSA-68rp-wp8r-4726"],
    )
    assert_that(result.output).starts_with(
        "Note: OSV-Scanner scanned the packages listed in these manifests but "
        "could not resolve their indirect dependencies, so those were not "
        f"checked: requirements.txt: package System(7):{_PRIVATE}: not found",
    )


@pytest.mark.parametrize(
    ("files", "stderr", "quoted"),
    [
        (
            {"requirements.txt": f"six==1.16.0\n{_PRIVATE}==1.2.3\n"},
            _PRIVATE_PACKAGE_STDERR,
            [f"requirements.txt: package System(7):{_PRIVATE}: not found"],
        ),
        (
            {"a/requirements.txt": "zz-a==1\n", "b/requirements.txt": "zz-b==1\n"},
            _TWO_PRIVATE_STDERR_2_7,
            ["a/requirements.txt: package System(7):zz-a", "b/requirements.txt"],
        ),
        (
            {"a/requirements.txt": "zz-a==1\n", "b/requirements.txt": "zz-b==1\n"},
            _TWO_PRIVATE_STDERR_2_4,
            ["b/requirements.txt: package System(7):zz-b", "a/requirements.txt"],
        ),
        (
            {"requirements.txt": "flask=====\n"},
            _MULTILINE_RESOLUTION_STDERR,
            ["resolution impossible:", 'no candidates at all for: flask "====="'],
        ),
        (
            {"pom.xml": "<project/>\n"},
            _MAVEN_STDERR,
            ["pom.xml: failed resolving", "zz.private:nothere"],
        ),
    ],
    ids=[
        "one-manifest",
        "two-manifests-2.7",
        "two-manifests-2.4",
        "multiline",
        "maven",
    ],
)
def test_failed_resolution_alone_is_a_clean_complete_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    files: dict[str, str],
    stderr: str,
    quoted: list[str],
) -> None:
    """Exit 127 caused only by a failed resolution is a clean verdict.

    osv-scanner exits 127 whenever it logged an error and found nothing; a
    failed transitive resolution is enough, with a healthy network.
    """
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files=files,
        returncode=127,
        stderr=stderr,
    )

    _assert_complete(result)
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).starts_with("Note: OSV-Scanner scanned")
    for text in quoted:
        assert_that(result.output).contains(text)
    assert_that(result.output).does_not_contain(str(tmp_path).lstrip("/"))


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "quoted"),
    [
        (
            127,
            _EMPTY_REPORT,
            _DEAD_NETWORK_STDERR_2_7,
            "OSV-Scanner could not extract every package source or look up its "
            "vulnerabilities, so the scan is incomplete: max retries exceeded",
        ),
        (
            127,
            "",
            _DEAD_NETWORK_STDERR_2_4,
            "OSV-Scanner exited with an error and printed no report: error when "
            "retrieving vulns: max retries exceeded",
        ),
    ],
    ids=["2.7.1", "2.4.0"],
)
def test_failed_resolution_with_the_network_down_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    returncode: int,
    stdout: str,
    stderr: str,
    quoted: str,
) -> None:
    """The vulnerability lookup failing too still makes the run incomplete."""
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={"requirements.txt": f"flask==3.0.0\n{_PRIVATE}==1.2.3\n"},
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )

    _assert_incomplete(result)
    # The quote starts at the lookup failure, not at the resolution warning.
    assert_that(result.output).starts_with(quoted)
    assert_that(result.output).does_not_contain(str(tmp_path).lstrip("/"))


@pytest.mark.parametrize(
    ("line", "files"),
    [
        (
            "Error during extraction: (extracting as transitivedependency/pomxml) "
            "failed to open {root}/pom.xml: permission denied\n",
            {"pom.xml": "<project/>\n"},
        ),
        (
            "Error during extraction: (extracting as transitivedependency/"
            "requirements) failed resolution for {root}/gone.txt: not found\n",
            {"requirements.txt": "six==1.16.0\n"},
        ),
        (
            "Error during extraction: (extracting as transitivedependency/"
            "javaarchive) failed resolution for {root}/app.jar: not found\n",
            {"app.jar": "jar\n"},
        ),
        (
            "Error during extraction: (extracting as transitivedependency/"
            "requirements) client factories not configured\n",
            {"requirements.txt": "six==1.16.0\n"},
        ),
    ],
    ids=["other-wording", "missing-file", "unknown-enricher", "no-path"],
)
def test_resolution_error_that_cannot_be_classified_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    line: str,
    files: dict[str, str],
) -> None:
    """Anything not plainly a failed resolution of a read manifest skips it."""
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files=files,
        returncode=127,
        stderr=_HEADER + _END + line,
    )

    _assert_incomplete(result)
    assert_that(result.output).starts_with(
        "OSV-Scanner could not extract every package source",
    )


def test_unexplained_error_beside_a_failed_resolution_is_incomplete(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """Exit 127 is a verdict only when the resolution failure explains it."""
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={"requirements.txt": f"six==1.16.0\n{_PRIVATE}==1.2.3\n"},
        returncode=127,
        stderr=_PRIVATE_PACKAGE_STDERR + "Failed to load the local database\n",
    )

    _assert_incomplete(result)
    assert_that(result.output).contains("Failed to load the local database")


def test_several_malformed_lockfiles_of_one_extractor_are_not_no_files(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """The multi-file error shape names every lockfile osv-scanner skipped."""
    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={"a/package-lock.json": "not json{", "b/package-lock.json": "garbage{"},
        returncode=128,
        stdout="",
        stderr=_TWO_MALFORMED_STDERR,
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(
        "a/package-lock.json: could not extract: invalid character 'o'",
    ).contains("b/package-lock.json: could not extract: invalid character 'g'")


def test_excluded_directory_does_not_hide_a_sibling_with_a_colon(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """``vendor: data/`` is not under an excluded, empty ``vendor/``."""
    osv_scanner_plugin.set_options(exclude_patterns=["vendor", "requirements.txt"])

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={
            "requirements.txt": "six==1.16.0\n",
            "vendor/": "",
            "vendor: data/package-lock.json": "not json{",
        },
        returncode=127,
        stderr=_HEADER + _END + _malformed("vendor: data/package-lock.json"),
    )

    _assert_incomplete(result)
    assert_that(result.output).contains("vendor: data/package-lock.json")


def test_failed_resolution_under_an_excluded_path_leaves_no_note(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """The exclusion reads the path out of ``failed resolution for <path>``."""
    osv_scanner_plugin.set_options(exclude_patterns=["vendor"])
    stderr = _PRIVATE_PACKAGE_STDERR.replace(
        "{root}/requirements.txt",
        "{root}/vendor/requirements.txt",
    )

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={"vendor/requirements.txt": f"{_PRIVATE}==1.2.3\n"},
        returncode=127,
        stderr=stderr,
    )

    _assert_complete(result)
    assert_that(result.success).is_true()
    assert_that(result.output).is_none()


def test_excluded_broken_lockfile_beside_a_clean_one_is_a_clean_scan(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """The exit 127 its error causes is excluded with it."""
    osv_scanner_plugin.set_options(exclude_patterns=["vendor"])

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={
            "requirements.txt": "six==1.16.0\n",
            "vendor/package-lock.json": "not json{",
        },
        returncode=127,
        stderr=_HEADER + _END + _malformed("vendor/package-lock.json"),
    )

    _assert_complete(result)
    assert_that(result.success).is_true()


@pytest.mark.parametrize(
    ("exclude", "kept"),
    [([], True), (["package-lock.json"], False)],
    ids=["not-excluded", "excluded"],
)
def test_path_with_a_colon_and_spaces_is_read_whole(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    exclude: list[str],
    kept: bool,
) -> None:
    """A source path holding ``": "`` and spaces is matched as one file."""
    osv_scanner_plugin.set_options(exclude_patterns=exclude)

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={
            "requirements.txt": "six==1.16.0\n",
            "we ird: dir/package-lock.json": "not json{",
        },
        returncode=127,
        stderr=_HEADER + _END + _malformed("we ird: dir/package-lock.json"),
    )

    assert_that(result.execution_failed).is_equal_to(kept)
    if kept:
        assert_that(result.output).contains(
            "incomplete: we ird: dir/package-lock.json: could not extract",
        )


def test_path_naming_two_files_keeps_the_error(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
) -> None:
    """When the path cannot be told apart, the error is never dropped."""
    osv_scanner_plugin.set_options(exclude_patterns=["package-lock.json"])

    result = _check(
        osv_scanner_plugin,
        tmp_path,
        files={
            "requirements.txt": "six==1.16.0\n",
            # Both "we ird" and "we ird: dir/package-lock.json" are files.
            "we ird": "",
            "we ird: dir/package-lock.json": "not json{",
        },
        returncode=127,
        stderr=_HEADER + _END + _malformed("we ird: dir/package-lock.json"),
    )

    _assert_incomplete(result)


@pytest.mark.parametrize("printed", ["real", "link"])
def test_exclusion_matches_either_spelling_of_a_symlinked_root(
    osv_scanner_plugin: OsvScannerPlugin,
    tmp_path: Path,
    printed: str,
) -> None:
    """The macOS ``/var`` versus ``/private/var`` form names the same file.

    lintro hands osv-scanner the resolved scan root, so errors name the real
    path while the user named the symlink.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    osv_scanner_plugin.set_options(exclude_patterns=["vendor"])
    files = {"requirements.txt": "six==1.16.0\n", "vendor/package-lock.json": "x{"}
    stderr = _HEADER + _END + _malformed("vendor/package-lock.json")
    shown = _malformed("vendor: data/package-lock.json")

    excluded = _check(
        osv_scanner_plugin,
        link,
        files=files,
        returncode=127,
        stderr=stderr,
        printed_root=real if printed == "real" else link,
    )
    unrelated = _check(
        osv_scanner_plugin,
        link,
        files={"vendor: data/package-lock.json": "x{"},
        returncode=127,
        stderr=_HEADER + _END + shown,
        printed_root=real if printed == "real" else link,
    )

    _assert_complete(excluded)
    _assert_incomplete(unrelated)
