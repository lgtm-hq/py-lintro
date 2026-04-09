"""Unit tests for parsing valid gitleaks output."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.parsers.gitleaks.gitleaks_parser import parse_gitleaks_output


def test_parse_gitleaks_valid_output() -> None:
    """Parse a representative Gitleaks JSON result and validate fields."""
    sample_output = json.dumps(
        [
            {
                "Description": "AWS Access Key",
                "StartLine": 10,
                "EndLine": 10,
                "StartColumn": 15,
                "EndColumn": 35,
                "Match": "AKIAIOSFODNN7EXAMPLE",
                # AWS example key from docs, not a real credential
                "Secret": "AKIAIOSFODNN7EXAMPLE",  # nosec B105
                "File": "config.py",
                "SymlinkFile": "",
                "Commit": "",
                "Entropy": 3.5,
                "Author": "",
                "Email": "",
                "Date": "",
                "Message": "",
                "Tags": ["key", "AWS"],
                "RuleID": "aws-access-key-id",
                "Fingerprint": "config.py:aws-access-key-id:10",
            },
        ],
    )

    issues = parse_gitleaks_output(output=sample_output)

    assert_that(len(issues)).is_equal_to(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("config.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.column).is_equal_to(15)
    assert_that(issue.end_line).is_equal_to(10)
    assert_that(issue.end_column).is_equal_to(35)
    assert_that(issue.rule_id).is_equal_to("aws-access-key-id")
    assert_that(issue.description).is_equal_to("AWS Access Key")
    assert_that(issue.secret).is_equal_to("AKIAIOSFODNN7EXAMPLE")
    assert_that(issue.entropy).is_equal_to(3.5)
    assert_that(issue.tags).is_equal_to(["key", "AWS"])
    assert_that(issue.fingerprint).is_equal_to("config.py:aws-access-key-id:10")


def test_parse_gitleaks_multiple_findings() -> None:
    """Parser should handle multiple findings."""
    sample_output = json.dumps(
        [
            {
                "Description": "AWS Access Key",
                "StartLine": 5,
                "EndLine": 5,
                "StartColumn": 1,
                "EndColumn": 20,
                "File": "a.py",
                "RuleID": "aws-access-key-id",
                "Fingerprint": "a.py:aws-access-key-id:5",
            },
            {
                "Description": "GitHub Token",
                "StartLine": 10,
                "EndLine": 10,
                "StartColumn": 1,
                "EndColumn": 40,
                "File": "b.py",
                "RuleID": "github-pat",
                "Fingerprint": "b.py:github-pat:10",
            },
        ],
    )

    issues = parse_gitleaks_output(output=sample_output)

    assert_that(len(issues)).is_equal_to(2)
    assert_that(issues[0].file).is_equal_to("a.py")
    assert_that(issues[0].rule_id).is_equal_to("aws-access-key-id")
    assert_that(issues[1].file).is_equal_to("b.py")
    assert_that(issues[1].rule_id).is_equal_to("github-pat")
