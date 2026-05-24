"""Unit tests for OSV-Scanner parser."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.osv_scanner.osv_scanner_parser import parse_osv_scanner_output


@pytest.mark.parametrize(
    ("output", "expected_count"),
    [
        pytest.param(None, 0, id="none_input"),
        pytest.param("", 0, id="empty_string"),
        pytest.param("   \n\n  ", 0, id="whitespace_only"),
    ],
)
def test_parse_empty_cases(output: str | None, expected_count: int) -> None:
    """Parser returns empty list for empty/None input."""
    result = parse_osv_scanner_output(output)
    assert_that(result).is_length(expected_count)


def test_parse_empty_results() -> None:
    """Empty results list returns no issues."""
    issues = parse_osv_scanner_output(json.dumps({"results": []}))
    assert_that(issues).is_equal_to([])


def test_parse_single_vulnerability() -> None:
    """Parser extracts single vulnerability correctly."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        {
                            "package": {
                                "name": "flask",
                                "version": "2.0.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-abcd-1234-efgh"],
                                    "max_severity": "HIGH",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-abcd-1234-efgh",
                                    "summary": "XSS in Flask",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    )
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("requirements.txt")
    assert_that(issues[0].vuln_id).is_equal_to("GHSA-abcd-1234-efgh")
    assert_that(issues[0].severity).is_equal_to("HIGH")
    assert_that(issues[0].package_name).is_equal_to("flask")
    assert_that(issues[0].package_version).is_equal_to("2.0.0")
    assert_that(issues[0].package_ecosystem).is_equal_to("PyPI")


def test_parse_multiple_vulnerabilities() -> None:
    """Parser handles multiple vulnerabilities across packages."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        {
                            "package": {
                                "name": "flask",
                                "version": "2.0.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-1111-aaaa-bbbb"],
                                    "max_severity": "HIGH",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-1111-aaaa-bbbb",
                                    "summary": "Issue 1",
                                },
                            ],
                        },
                        {
                            "package": {
                                "name": "django",
                                "version": "3.0.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-2222-cccc-dddd"],
                                    "max_severity": "CRITICAL",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-2222-cccc-dddd",
                                    "summary": "Issue 2",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    )
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].package_name).is_equal_to("flask")
    assert_that(issues[0].severity).is_equal_to("HIGH")
    assert_that(issues[1].package_name).is_equal_to("django")
    assert_that(issues[1].severity).is_equal_to("CRITICAL")


def test_parse_vulnerability_with_multiple_ids() -> None:
    """Parser uses first ID as primary when group has multiple IDs."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "package-lock.json"},
                    "packages": [
                        {
                            "package": {
                                "name": "lodash",
                                "version": "4.17.15",
                                "ecosystem": "npm",
                            },
                            "groups": [
                                {
                                    "ids": [
                                        "GHSA-xxxx-yyyy-zzzz",
                                        "CVE-2021-23337",
                                    ],
                                    "max_severity": "CRITICAL",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-xxxx-yyyy-zzzz",
                                    "summary": "Prototype pollution",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    )
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].vuln_id).is_equal_to("GHSA-xxxx-yyyy-zzzz")


def test_parse_vulnerability_with_fixed_version() -> None:
    """Parser extracts fixed version from affected data."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        {
                            "package": {
                                "name": "requests",
                                "version": "2.25.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-test-1234-abcd"],
                                    "max_severity": "MEDIUM",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-test-1234-abcd",
                                    "summary": "Test vuln",
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
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].fixed_version).is_equal_to("2.32.0")
    assert_that(issues[0].message).contains("fix: 2.32.0")


def test_parse_invalid_json() -> None:
    """Invalid JSON returns empty list without crashing."""
    issues = parse_osv_scanner_output("not valid json")
    assert_that(issues).is_equal_to([])


def test_parse_json_with_leading_log_lines() -> None:
    """Leading osv-scanner log lines before JSON do not break parsing."""
    output = (
        "Scanning dir /tmp/example\n"
        "Starting filesystem walk for root: /\n"
        '{"results": [], "experimental_config": {"licenses": {"summary": false}}}\n'
    )
    issues = parse_osv_scanner_output(output)
    assert_that(issues).is_equal_to([])


def test_parse_non_object_json() -> None:
    """Non-object JSON returns empty list."""
    issues = parse_osv_scanner_output(json.dumps([1, 2, 3]))
    assert_that(issues).is_equal_to([])


def test_parse_non_list_results() -> None:
    """Non-list results returns empty list."""
    issues = parse_osv_scanner_output(json.dumps({"results": "not a list"}))
    assert_that(issues).is_equal_to([])


def test_parse_missing_results_key() -> None:
    """Missing results key returns empty list."""
    issues = parse_osv_scanner_output(json.dumps({}))
    assert_that(issues).is_equal_to([])


def test_parse_malformed_package_entry() -> None:
    """Malformed package entries are skipped gracefully."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        None,
                        42,
                        {"package": "not a dict"},
                        {"package": {"name": ""}},
                    ],
                },
            ],
        },
    )
    issues = parse_osv_scanner_output(output)
    assert_that(issues).is_equal_to([])


def test_issue_display_row() -> None:
    """OsvScannerIssue.to_display_row returns correct values."""
    from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue

    issue = OsvScannerIssue(
        file="requirements.txt",
        line=0,
        column=0,
        vuln_id="GHSA-test-1234",
        severity="HIGH",
        package_name="requests",
        package_version="2.25.0",
        package_ecosystem="PyPI",
    )
    row = issue.to_display_row()
    assert_that(row["file"]).is_equal_to("requirements.txt")
    assert_that(row["code"]).is_equal_to("GHSA-test-1234")
    # "HIGH" is normalized to "ERROR" by SeverityLevel
    assert_that(row["severity"]).is_equal_to("ERROR")
    assert_that(row["message"]).contains("GHSA-test-1234")
    assert_that(row["message"]).contains("requests@2.25.0")


def test_issue_message_format() -> None:
    """Issue message includes vuln ID, package info, and fix version."""
    from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue

    issue = OsvScannerIssue(
        vuln_id="GHSA-abcd-1234",
        package_name="flask",
        package_version="2.0.0",
        fixed_version="2.3.0",
    )
    assert_that(issue.message).is_equal_to(
        "[GHSA-abcd-1234] flask@2.0.0 (fix: 2.3.0)",
    )


def test_issue_message_format_no_fix() -> None:
    """Issue message omits fix when no fixed version available."""
    from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue

    issue = OsvScannerIssue(
        vuln_id="GHSA-abcd-1234",
        package_name="flask",
        package_version="2.0.0",
    )
    assert_that(issue.message).is_equal_to("[GHSA-abcd-1234] flask@2.0.0")


def test_parse_fallthrough_vuln_id_lookup() -> None:
    """Parser finds fixed version even when primary ID is not in vulnerabilities.

    When the first group ID (e.g. a CVE alias) doesn't have a matching entry
    in the vulnerabilities array, the parser should fall through to subsequent
    IDs to find the vulnerability details and fixed version.
    """
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        {
                            "package": {
                                "name": "requests",
                                "version": "2.25.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": [
                                        "CVE-2024-99999",
                                        "GHSA-abcd-1234-efgh",
                                    ],
                                    "max_severity": "HIGH",
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-abcd-1234-efgh",
                                    "summary": "Session bypass",
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
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].vuln_id).is_equal_to("CVE-2024-99999")
    assert_that(issues[0].fixed_version).is_equal_to("2.32.0")


def test_default_severity() -> None:
    """Default severity is MEDIUM when not specified in groups."""
    output = json.dumps(
        {
            "results": [
                {
                    "source": {"path": "requirements.txt"},
                    "packages": [
                        {
                            "package": {
                                "name": "foo",
                                "version": "1.0.0",
                                "ecosystem": "PyPI",
                            },
                            "groups": [
                                {
                                    "ids": ["GHSA-no-severity"],
                                },
                            ],
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-no-severity",
                                    "summary": "No severity",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    )
    issues = parse_osv_scanner_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("MEDIUM")
