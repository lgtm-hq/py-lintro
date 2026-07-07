"""Unit tests for the Trivy plugin (definition, command, and check flow)."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.trivy import TrivyPlugin

_VERIFY_VERSION = "lintro.plugins.execution_preparation.verify_tool_version"
_SUBPROCESS_RUN = "lintro.tools.definitions.trivy.subprocess.run"


def _report(vulnerabilities: list[dict[str, Any]]) -> str:
    """Build a minimal single-target Trivy JSON report.

    Args:
        vulnerabilities: Vulnerability records to embed.

    Returns:
        JSON string for stdout.
    """
    return json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Class": "lang-pkgs",
                    "Type": "pip",
                    "Vulnerabilities": vulnerabilities,
                },
            ],
        },
    )


_VULN = {
    "VulnerabilityID": "CVE-2019-14234",
    "PkgName": "Django",
    "InstalledVersion": "2.2.0",
    "FixedVersion": "2.2.4",
    "Severity": "CRITICAL",
    "Title": "Django: SQL injection",
    "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2019-14234",
}


def test_definition_metadata(trivy_plugin: TrivyPlugin) -> None:
    """The definition advertises the expected security metadata."""
    definition = trivy_plugin.definition

    assert_that(definition.name).is_equal_to("trivy")
    assert_that(definition.can_fix).is_false()
    assert_that(bool(definition.tool_type & ToolType.SECURITY)).is_true()
    assert_that(definition.file_patterns).contains("requirements.txt")
    assert_that(definition.file_patterns).contains("go.mod")


def test_file_patterns_exclude_iac_and_dockerfiles(trivy_plugin: TrivyPlugin) -> None:
    """Trivy must not claim Dockerfiles (hadolint) or *.tf (checkov)."""
    patterns = trivy_plugin.definition.file_patterns

    assert_that(patterns).does_not_contain("Dockerfile")
    assert_that(patterns).does_not_contain("Dockerfile*")
    assert_that(patterns).does_not_contain("*.tf")


def test_build_command_is_hermetic(trivy_plugin: TrivyPlugin) -> None:
    """The check command runs offline with vuln scanners only."""
    cmd = trivy_plugin._build_check_command("requirements.txt")

    assert_that(cmd[:3]).is_equal_to(["trivy", "fs", "--scanners"])
    assert_that(cmd).contains("vuln")
    assert_that(cmd).contains("--format", "json")
    assert_that(cmd).contains("--skip-db-update")
    assert_that(cmd).contains("--offline-scan")
    # Target file is the final argument.
    assert_that(cmd[-1]).is_equal_to("requirements.txt")


def test_build_command_threads_severity_and_unfixed() -> None:
    """severity / ignore_unfixed options are threaded into the command."""
    plugin = TrivyPlugin()
    plugin.set_options(severity=["CRITICAL", "HIGH"], ignore_unfixed=True)

    cmd = plugin._build_check_command("requirements.txt")
    assert_that(cmd).contains("--severity", "CRITICAL,HIGH")
    assert_that(cmd).contains("--ignore-unfixed")


def test_options_can_disable_hermetic_flags() -> None:
    """Disabling skip_db_update / offline_scan removes the hermetic flags."""
    plugin = TrivyPlugin()
    plugin.set_options(skip_db_update=False, offline_scan=False)

    cmd = plugin._build_check_command("requirements.txt")
    assert_that(cmd).does_not_contain("--skip-db-update")
    assert_that(cmd).does_not_contain("--offline-scan")


def test_doc_url_builds_advisory_link(trivy_plugin: TrivyPlugin) -> None:
    """doc_url builds an Aqua advisory URL from a CVE id."""
    assert_that(trivy_plugin.doc_url("CVE-2019-14234")).is_equal_to(
        "https://avd.aquasec.com/nvd/cve-2019-14234",
    )
    assert_that(trivy_plugin.doc_url("")).is_none()


def _run_check(plugin: TrivyPlugin, req_file: Path, completed: CompletedProcess[str]):
    """Run plugin.check with version verification and subprocess mocked.

    Args:
        plugin: The plugin under test.
        req_file: A real ``requirements.txt`` path so file discovery keeps it.
        completed: The mocked subprocess result.

    Returns:
        The ToolResult from the mocked run.
    """
    with (
        patch(_VERIFY_VERSION, return_value=None),
        patch(_SUBPROCESS_RUN, return_value=completed),
    ):
        return plugin.check([str(req_file)], {})


def test_check_reports_vulnerabilities(
    trivy_plugin: TrivyPlugin,
    tmp_path: Path,
) -> None:
    """Vulnerabilities are surfaced as issues and mark the run unsuccessful."""
    req = tmp_path / "requirements.txt"
    req.write_text("Django==2.2.0\n")

    completed = CompletedProcess(
        args=["trivy"],
        returncode=0,
        stdout=_report([_VULN]),
        stderr="",
    )

    result = _run_check(trivy_plugin, req, completed)
    assert_that(result.name).is_equal_to("trivy")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues[0].vuln_id).is_equal_to("CVE-2019-14234")


def test_check_clean_scan_succeeds(
    trivy_plugin: TrivyPlugin,
    tmp_path: Path,
) -> None:
    """A report with no vulnerabilities is a successful run."""
    req = tmp_path / "requirements.txt"
    req.write_text("flask==3.0.0\n")

    completed = CompletedProcess(
        args=["trivy"],
        returncode=0,
        stdout=json.dumps({"SchemaVersion": 2, "ArtifactName": "."}),
        stderr="",
    )

    result = _run_check(trivy_plugin, req, completed)
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_missing_db_reports_non_fatal_skip(
    trivy_plugin: TrivyPlugin,
    tmp_path: Path,
) -> None:
    """A missing vulnerability DB is a non-blocking skip, not a failure."""
    req = tmp_path / "requirements.txt"
    req.write_text("Django==2.2.0\n")

    completed = CompletedProcess(
        args=["trivy"],
        returncode=1,
        stdout="",
        stderr="FATAL vulnerability DB needs to be updated; run trivy",
    )

    result = _run_check(trivy_plugin, req, completed)
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("vulnerability database not found")


def test_check_genuine_error_fails_closed(
    trivy_plugin: TrivyPlugin,
    tmp_path: Path,
) -> None:
    """A non-DB error with a non-zero exit fails closed (security tool)."""
    req = tmp_path / "requirements.txt"
    req.write_text("Django==2.2.0\n")

    completed = CompletedProcess(
        args=["trivy"],
        returncode=2,
        stdout="",
        stderr="permission denied reading target",
    )

    result = _run_check(trivy_plugin, req, completed)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_unparseable_stdout_fails_closed(
    trivy_plugin: TrivyPlugin,
    tmp_path: Path,
) -> None:
    """Unparseable stdout with no findings must not report a clean pass."""
    req = tmp_path / "requirements.txt"
    req.write_text("Django==2.2.0\n")

    completed = CompletedProcess(
        args=["trivy"],
        returncode=0,
        stdout="not json at all",
        stderr="",
    )

    result = _run_check(trivy_plugin, req, completed)
    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_fix_raises_not_implemented(trivy_plugin: TrivyPlugin) -> None:
    """Trivy does not support autofix."""
    assert_that(trivy_plugin.fix).raises(NotImplementedError).when_called_with(
        ["requirements.txt"],
        {},
    )
