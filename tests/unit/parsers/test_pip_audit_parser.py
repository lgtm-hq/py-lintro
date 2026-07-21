"""Unit tests for the pip-audit parser.

The fixtures here mirror the exact JSON schema emitted by
``pip-audit --format json`` (a single top-level object with ``dependencies``
and ``fixes``). They are captured/synthetic so the tests never depend on live
vulnerability-database results.
"""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.pip_audit.pip_audit_issue import PipAuditIssue
from lintro.parsers.pip_audit.pip_audit_parser import parse_pip_audit_output

# A realistic pip-audit JSON payload: one vulnerable dependency (two vulns),
# one clean dependency, and one skipped dependency.
_SAMPLE_OUTPUT = json.dumps(
    {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.4.1",
                "vulns": [
                    {
                        "id": "PYSEC-2019-217",
                        "fix_versions": ["2.10.1"],
                        "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45"],
                        "description": "Jinja2 sandbox escape via str.format.",
                    },
                    {
                        "id": "PYSEC-2019-218",
                        "fix_versions": ["2.10.1", "2.11.0"],
                        "aliases": ["CVE-2019-8341"],
                        "description": "Server-side template injection.",
                    },
                ],
            },
            {
                "name": "packaging",
                "version": "25.0",
                "vulns": [],
            },
            {
                "name": "somepkg",
                "skip_reason": "Dependency not found on PyPI and could not be audited",
            },
        ],
        "fixes": [],
    },
)


def test_issue_model_message_and_display_map() -> None:
    """The issue model builds a message and maps code/severity for display."""
    issue = PipAuditIssue(
        file="requirements.txt",
        vuln_id="PYSEC-2019-217",
        package_name="jinja2",
        package_version="2.4.1",
        fix_versions=["2.10.1"],
        aliases=["CVE-2019-10906"],
    )

    assert_that(issue.message).is_equal_to(
        "[PYSEC-2019-217] jinja2@2.4.1: fix available in 2.10.1",
    )
    row = issue.to_display_row()
    assert_that(row.get("code")).is_equal_to("PYSEC-2019-217")


def test_issue_model_no_fix_versions_message() -> None:
    """The message reports 'no known fix' when there are no fix versions."""
    issue = PipAuditIssue(
        vuln_id="PYSEC-2020-1",
        package_name="foo",
        package_version="1.0.0",
        fix_versions=[],
    )
    assert_that(issue.message).contains("no known fix")


@pytest.mark.parametrize(
    ("output", "expected_count"),
    [
        pytest.param(None, 0, id="none_input"),
        pytest.param("", 0, id="empty_string"),
        pytest.param("   \n\n  ", 0, id="whitespace_only"),
        pytest.param("not json at all", 0, id="malformed_json"),
        pytest.param("[]", 0, id="json_array_root"),
        pytest.param('{"dependencies": {}}', 0, id="dependencies_not_list"),
        pytest.param('{"fixes": []}', 0, id="missing_dependencies"),
    ],
)
def test_parse_invalid_or_empty_input(
    output: str | None,
    expected_count: int,
) -> None:
    """Parser returns an empty list for empty, null, or malformed input.

    Args:
        output: The raw input to parse.
        expected_count: Expected number of parsed issues.
    """
    result = parse_pip_audit_output(output)
    assert_that(result).is_length(expected_count)


def test_parse_no_vulnerabilities() -> None:
    """Parser returns no issues when every dependency is clean."""
    output = json.dumps(
        {
            "dependencies": [
                {"name": "packaging", "version": "25.0", "vulns": []},
            ],
            "fixes": [],
        },
    )
    assert_that(parse_pip_audit_output(output)).is_length(0)


def test_parse_field_extraction() -> None:
    """Parser extracts package, version, vuln id, fixes, and aliases."""
    result = parse_pip_audit_output(_SAMPLE_OUTPUT, source="requirements.txt")

    # Two vulns on jinja2; packaging is clean; somepkg is skipped.
    assert_that(result).is_length(2)

    first = result[0]
    assert_that(first.package_name).is_equal_to("jinja2")
    assert_that(first.package_version).is_equal_to("2.4.1")
    assert_that(first.vuln_id).is_equal_to("PYSEC-2019-217")
    assert_that(first.fix_versions).contains("2.10.1")
    assert_that(first.aliases).contains("CVE-2019-10906")
    assert_that(first.file).is_equal_to("requirements.txt")
    assert_that(first.description).contains("sandbox escape")


def test_parse_multiple_vulns_per_package() -> None:
    """Parser emits one issue per (dependency, vulnerability) pair."""
    result = parse_pip_audit_output(_SAMPLE_OUTPUT)
    ids = [issue.vuln_id for issue in result]
    assert_that(ids).is_equal_to(["PYSEC-2019-217", "PYSEC-2019-218"])


def test_parse_multiple_vulnerable_packages() -> None:
    """Parser aggregates vulns across multiple vulnerable dependencies."""
    output = json.dumps(
        {
            "dependencies": [
                {
                    "name": "jinja2",
                    "version": "2.4.1",
                    "vulns": [{"id": "PYSEC-2019-217", "fix_versions": []}],
                },
                {
                    "name": "requests",
                    "version": "2.19.0",
                    "vulns": [{"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"]}],
                },
            ],
            "fixes": [],
        },
    )
    result = parse_pip_audit_output(output)
    assert_that(result).is_length(2)
    assert_that([i.package_name for i in result]).contains("jinja2", "requests")


def test_parse_source_defaults_to_package_name() -> None:
    """When no source is given, the issue file falls back to the package name."""
    output = json.dumps(
        {
            "dependencies": [
                {
                    "name": "jinja2",
                    "version": "2.4.1",
                    "vulns": [{"id": "PYSEC-2019-217", "fix_versions": []}],
                },
            ],
        },
    )
    result = parse_pip_audit_output(output)
    assert_that(result[0].file).is_equal_to("jinja2")


def test_parse_skips_vuln_without_id() -> None:
    """A vulnerability entry with an empty id is skipped."""
    output = json.dumps(
        {
            "dependencies": [
                {
                    "name": "jinja2",
                    "version": "2.4.1",
                    "vulns": [
                        {"id": "", "fix_versions": []},
                        {"id": "PYSEC-2019-217", "fix_versions": []},
                    ],
                },
            ],
        },
    )
    result = parse_pip_audit_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].vuln_id).is_equal_to("PYSEC-2019-217")


def test_parse_coerces_non_string_fix_versions() -> None:
    """Non-string entries in fix_versions/aliases are dropped defensively."""
    output = json.dumps(
        {
            "dependencies": [
                {
                    "name": "jinja2",
                    "version": "2.4.1",
                    "vulns": [
                        {
                            "id": "PYSEC-2019-217",
                            "fix_versions": ["2.10.1", 5, None],
                            "aliases": ["CVE-2019-10906", 42],
                        },
                    ],
                },
            ],
        },
    )
    result = parse_pip_audit_output(output)
    assert_that(result[0].fix_versions).is_equal_to(["2.10.1"])
    assert_that(result[0].aliases).is_equal_to(["CVE-2019-10906"])
