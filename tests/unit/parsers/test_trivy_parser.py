"""Unit tests for the Trivy output parser.

Fixtures are trimmed from real ``trivy fs --scanners vuln --format json`` output
captured on a seeded ``requirements.txt`` (trivy 0.72.0).
"""

from __future__ import annotations

import json
from typing import Any

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.trivy.trivy_issue import TrivyIssue
from lintro.parsers.trivy.trivy_parser import parse_trivy_output


def _report(
    vulnerabilities: list[dict[str, Any]],
    target: str = "requirements.txt",
) -> str:
    """Wrap vulnerability records in a single-result Trivy report.

    Args:
        vulnerabilities: Vulnerability records to embed.
        target: The scanned lockfile / manifest name.

    Returns:
        JSON string mimicking ``trivy fs --format json`` for one target.
    """
    return json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {
                    "Target": target,
                    "Class": "lang-pkgs",
                    "Type": "pip",
                    "Vulnerabilities": vulnerabilities,
                },
            ],
        },
    )


_REAL_VULN: dict[str, Any] = {
    "VulnerabilityID": "CVE-2019-14234",
    "VendorIDs": ["GHSA-6r97-cj55-9hrq"],
    "PkgName": "Django",
    "InstalledVersion": "2.2.0",
    "FixedVersion": "1.11.23, 2.1.11, 2.2.4",
    "Status": "fixed",
    "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2019-14234",
    "Title": "Django: SQL injection possibility in key and index lookups",
    "Severity": "CRITICAL",
    "CweIDs": ["CWE-89"],
}


def test_parses_real_vulnerability() -> None:
    """A real vulnerability maps to a fully populated TrivyIssue."""
    issues = parse_trivy_output(_report([_REAL_VULN]))

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(TrivyIssue)
    assert_that(issue.vuln_id).is_equal_to("CVE-2019-14234")
    assert_that(issue.pkg_name).is_equal_to("Django")
    assert_that(issue.installed_version).is_equal_to("2.2.0")
    assert_that(issue.fixed_version).is_equal_to("1.11.23, 2.1.11, 2.2.4")
    assert_that(issue.severity).is_equal_to("CRITICAL")
    assert_that(issue.target).is_equal_to("requirements.txt")
    assert_that(issue.file).is_equal_to("requirements.txt")


def test_doc_url_from_primary_url() -> None:
    """The finding's PrimaryURL is captured as the issue doc_url."""
    issue = parse_trivy_output(_report([_REAL_VULN]))[0]

    assert_that(issue.doc_url).is_equal_to(
        "https://avd.aquasec.com/nvd/cve-2019-14234",
    )


def test_message_includes_package_and_fix() -> None:
    """The composed message carries package, version, title and remediation."""
    issue = parse_trivy_output(_report([_REAL_VULN]))[0]

    assert_that(issue.message).contains("Django 2.2.0")
    assert_that(issue.message).contains("fixed in 1.11.23, 2.1.11, 2.2.4")


def test_unfixed_vulnerability_notes_no_fix() -> None:
    """A vulnerability without a FixedVersion reports 'no fix available'."""
    vuln = {**_REAL_VULN}
    vuln.pop("FixedVersion")
    issue = parse_trivy_output(_report([vuln]))[0]

    assert_that(issue.fixed_version).is_none()
    assert_that(issue.message).contains("no fix available")


def test_severity_normalizes_via_alias_table() -> None:
    """Native Trivy severities normalize to lintro SeverityLevel values."""
    critical = parse_trivy_output(_report([{**_REAL_VULN, "Severity": "CRITICAL"}]))[0]
    medium = parse_trivy_output(_report([{**_REAL_VULN, "Severity": "MEDIUM"}]))[0]
    low = parse_trivy_output(_report([{**_REAL_VULN, "Severity": "LOW"}]))[0]

    assert_that(critical.get_severity()).is_equal_to(SeverityLevel.ERROR)
    assert_that(medium.get_severity()).is_equal_to(SeverityLevel.WARNING)
    assert_that(low.get_severity()).is_equal_to(SeverityLevel.INFO)


def test_multiple_vulnerabilities_across_targets() -> None:
    """Vulnerabilities from multiple result blocks are all collected."""
    report = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Vulnerabilities": [_REAL_VULN],
                },
                {
                    "Target": "package-lock.json",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2020-8203",
                            "PkgName": "lodash",
                            "InstalledVersion": "4.17.15",
                            "FixedVersion": "4.17.19",
                            "Severity": "HIGH",
                            "Title": "lodash: prototype pollution",
                            "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2020-8203",
                        },
                    ],
                },
            ],
        },
    )

    issues = parse_trivy_output(report)
    assert_that(issues).is_length(2)
    assert_that({i.vuln_id for i in issues}).contains(
        "CVE-2019-14234",
        "CVE-2020-8203",
    )
    lodash = next(i for i in issues if i.pkg_name == "lodash")
    assert_that(lodash.target).is_equal_to("package-lock.json")


def test_result_without_vulnerabilities_yields_no_issues() -> None:
    """A clean result block (no Vulnerabilities key) yields no issues."""
    report = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [{"Target": "requirements.txt", "Class": "lang-pkgs"}],
        },
    )

    assert_that(parse_trivy_output(report)).is_empty()


def test_clean_scan_without_results_key() -> None:
    """A clean scan omits the Results key entirely and yields no issues."""
    report = json.dumps({"SchemaVersion": 2, "ArtifactName": "."})

    assert_that(parse_trivy_output(report)).is_empty()


def test_vulnerability_missing_id_is_skipped() -> None:
    """A vulnerability record without an identifier is skipped, not raised."""
    vuln = {**_REAL_VULN}
    vuln.pop("VulnerabilityID")

    assert_that(parse_trivy_output(_report([vuln]))).is_empty()


def test_none_input_returns_empty() -> None:
    """None input returns an empty list."""
    assert_that(parse_trivy_output(None)).is_empty()


def test_empty_string_returns_empty() -> None:
    """Empty / whitespace input returns an empty list."""
    assert_that(parse_trivy_output("")).is_empty()
    assert_that(parse_trivy_output("   \n  ")).is_empty()


def test_malformed_json_returns_empty() -> None:
    """Malformed JSON returns an empty list rather than raising."""
    assert_that(parse_trivy_output("{not valid json")).is_empty()


def test_non_object_root_returns_empty() -> None:
    """A JSON array root (not an object) returns an empty list."""
    assert_that(parse_trivy_output("[1, 2, 3]")).is_empty()


def test_results_not_a_list_returns_empty() -> None:
    """A non-list Results value returns an empty list."""
    assert_that(parse_trivy_output('{"Results": "nope"}')).is_empty()
