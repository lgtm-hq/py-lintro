"""Unit tests for the Checkov output parser.

Fixtures are trimmed from real ``checkov --output json --compact`` output
captured on a seeded Terraform sample (checkov 3.3.6). Note that ``severity``
and ``guideline`` are ``null`` unless Checkov runs with a platform API key.
"""

from __future__ import annotations

import json
from typing import Any

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.checkov.checkov_issue import CheckovIssue
from lintro.parsers.checkov.checkov_parser import (
    CHECKOV_PARSE_ERROR_CODE,
    parse_checkov_output,
)


def _report(failed: list[dict[str, Any]]) -> str:
    """Wrap failed-check records in a single-framework Checkov report.

    Args:
        failed: Failed-check records to embed.

    Returns:
        JSON string mimicking ``checkov --output json`` for one framework.
    """
    return json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "passed_checks": [],
                "failed_checks": failed,
                "skipped_checks": [],
                "parsing_errors": [],
            },
            "summary": {"passed": 0, "failed": len(failed), "skipped": 0},
        },
    )


_REAL_FAILED_CHECK: dict[str, Any] = {
    "check_id": "CKV_AWS_260",
    "check_name": "Ensure no security groups allow ingress from 0.0.0.0:0 to port 80",
    "check_result": {"result": "FAILED"},
    "file_path": "/checkov_violations.tf",
    "file_abs_path": "/repo/checkov_violations.tf",
    "file_line_range": [10, 19],
    "resource": "aws_security_group.allow_all",
    "check_class": "checkov.terraform.checks.resource.aws.SecurityGroupUnrestrictedIngress80",
    "severity": None,
    "guideline": None,
}


def test_parses_real_failed_check() -> None:
    """A real failed check maps to a fully populated CheckovIssue."""
    issues = parse_checkov_output(_report([_REAL_FAILED_CHECK]))

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(CheckovIssue)
    assert_that(issue.check_id).is_equal_to("CKV_AWS_260")
    assert_that(issue.file).is_equal_to("/repo/checkov_violations.tf")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.end_line).is_equal_to(19)
    assert_that(issue.resource).is_equal_to("aws_security_group.allow_all")


def test_message_includes_resource_attribution() -> None:
    """The display message carries the resource address."""
    issues = parse_checkov_output(_report([_REAL_FAILED_CHECK]))

    assert_that(issues[0].message).contains("aws_security_group.allow_all")
    assert_that(issues[0].message).contains("port 80")


def test_display_row_maps_check_id_to_code() -> None:
    """DISPLAY_FIELD_MAP routes check_id to the code column."""
    issue = parse_checkov_output(_report([_REAL_FAILED_CHECK]))[0]

    row = issue.to_display_row()
    assert_that(row["code"]).is_equal_to("CKV_AWS_260")
    assert_that(row["file"]).is_equal_to("/repo/checkov_violations.tf")


def test_missing_severity_falls_back_to_default() -> None:
    """Without a platform key, severity is null and defaults to WARNING."""
    issue = parse_checkov_output(_report([_REAL_FAILED_CHECK]))[0]

    assert_that(issue.severity).is_none()
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_native_severity_is_normalized() -> None:
    """When a platform key populates severity, it is honored and normalized."""
    check = {**_REAL_FAILED_CHECK, "severity": "HIGH"}

    issue = parse_checkov_output(_report([check]))[0]
    assert_that(issue.severity).is_equal_to("HIGH")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_guideline_populates_doc_url() -> None:
    """A guideline URL (platform key) is surfaced as the issue doc_url."""
    url = (
        "https://docs.prismacloud.io/en/enterprise-edition/policy-reference/CKV_AWS_260"
    )
    check = {**_REAL_FAILED_CHECK, "guideline": url}

    issue = parse_checkov_output(_report([check]))[0]
    assert_that(issue.guideline).is_equal_to(url)
    assert_that(issue.doc_url).is_equal_to(url)


def test_absent_guideline_leaves_doc_url_empty() -> None:
    """Without a guideline the parser leaves doc_url for the plugin fallback."""
    issue = parse_checkov_output(_report([_REAL_FAILED_CHECK]))[0]

    assert_that(issue.doc_url).is_equal_to("")


def test_multi_framework_list_output_is_flattened() -> None:
    """Checkov emits a list when several frameworks run; all are parsed."""
    tf_block = json.loads(_report([_REAL_FAILED_CHECK]))
    secrets_block = json.loads(
        _report(
            [
                {
                    "check_id": "CKV_SECRET_6",
                    "check_name": "Base64 High Entropy String",
                    "file_path": "/checkov_violations.tf",
                    "file_line_range": [3, 3],
                    "resource": "secret",
                },
            ],
        ),
    )
    secrets_block["check_type"] = "secrets"

    issues = parse_checkov_output(json.dumps([tf_block, secrets_block]))
    assert_that(issues).is_length(2)
    assert_that({i.check_id for i in issues}).is_equal_to(
        {"CKV_AWS_260", "CKV_SECRET_6"},
    )


def test_prefers_file_abs_path_over_repo_relative_file_path() -> None:
    """Checkov file_path is often a fake absolute path; use file_abs_path."""
    issues = parse_checkov_output(_report([_REAL_FAILED_CHECK]))

    assert_that(issues[0].file).is_equal_to("/repo/checkov_violations.tf")


def test_falls_back_to_file_path_when_abs_path_missing() -> None:
    """file_path is used when Checkov omits file_abs_path."""
    check = {
        "check_id": "CKV_AWS_1",
        "check_name": "name",
        "file_path": "/only.tf",
        "file_line_range": [1, 1],
        "resource": "r",
    }

    issues = parse_checkov_output(_report([check]))
    assert_that(issues[0].file).is_equal_to("/only.tf")


def test_passed_and_skipped_checks_are_ignored() -> None:
    """Only failed checks become issues."""
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "passed_checks": [{"check_id": "CKV_AWS_1"}],
                "failed_checks": [_REAL_FAILED_CHECK],
                "skipped_checks": [{"check_id": "CKV_AWS_2"}],
            },
        },
    )

    issues = parse_checkov_output(report)
    assert_that(issues).is_length(1)
    assert_that(issues[0].check_id).is_equal_to("CKV_AWS_260")


def test_checks_missing_required_fields_are_skipped() -> None:
    """Records lacking check_id or file_path are dropped, not crashed on."""
    bad = [
        {"check_name": "no id", "file_path": "/a.tf"},
        {"check_id": "CKV_AWS_9", "check_name": "no path"},
        _REAL_FAILED_CHECK,
    ]

    issues = parse_checkov_output(_report(bad))
    assert_that(issues).is_length(1)
    assert_that(issues[0].check_id).is_equal_to("CKV_AWS_260")


def test_malformed_line_range_defaults_safely() -> None:
    """Non-integer / empty line ranges do not raise."""
    check = {**_REAL_FAILED_CHECK, "file_line_range": ["x", None]}

    issue = parse_checkov_output(_report([check]))[0]
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.end_line).is_none()


def test_none_input_returns_empty_list() -> None:
    """None input yields an empty list."""
    assert_that(parse_checkov_output(None)).is_empty()


def test_empty_string_returns_empty_list() -> None:
    """Empty / whitespace input yields an empty list."""
    assert_that(parse_checkov_output("")).is_empty()
    assert_that(parse_checkov_output("   \n  ")).is_empty()


def test_malformed_json_returns_empty_list() -> None:
    """Invalid JSON is handled defensively without raising."""
    assert_that(parse_checkov_output("{not valid json")).is_empty()


def test_unexpected_structure_returns_empty_list() -> None:
    """A JSON payload without a results mapping yields no issues."""
    assert_that(parse_checkov_output(json.dumps({"summary": {}}))).is_empty()
    assert_that(parse_checkov_output(json.dumps([1, 2, 3]))).is_empty()


def test_non_dict_failed_check_entries_are_skipped() -> None:
    """Malformed entries are dropped without losing the valid ones.

    Checkov output is trusted input today, but a partially written report (a
    truncated stream, a future schema change) must not take the whole scan
    down: the valid findings still have to reach the report.
    """
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "failed_checks": [
                    "not-a-dict",
                    None,
                    42,
                    _REAL_FAILED_CHECK,
                ],
            },
        },
    )

    issues = parse_checkov_output(report)

    assert_that(issues).is_length(1)
    assert_that(issues[0].check_id).is_equal_to("CKV_AWS_260")


def test_parsing_errors_are_surfaced_as_issues() -> None:
    """A file checkov could not parse is reported, not silently dropped.

    Checkov derives its exit code from failed checks alone, so a report with
    an unreadable file lists it under ``parsing_errors``, leaves
    ``failed_checks`` empty and exits 0. Ignoring it would report a clean
    security scan for a file no policy ever ran against.
    """
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "failed_checks": [],
                "parsing_errors": ["/repo/broken.tf"],
            },
        },
    )

    issues = parse_checkov_output(report)

    assert_that(issues).is_length(1)
    assert_that(issues[0].check_id).is_equal_to(CHECKOV_PARSE_ERROR_CODE)
    assert_that(issues[0].file).is_equal_to("/repo/broken.tf")
    assert_that(issues[0].message).contains("could not parse")


def test_parsing_errors_are_reported_alongside_failed_checks() -> None:
    """Both kinds of finding survive in one report."""
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "failed_checks": [_REAL_FAILED_CHECK],
                "parsing_errors": ["/repo/broken.tf"],
            },
        },
    )

    codes = {issue.check_id for issue in parse_checkov_output(report)}

    assert_that(codes).is_equal_to({"CKV_AWS_260", CHECKOV_PARSE_ERROR_CODE})


def test_malformed_parsing_error_entries_are_skipped() -> None:
    """Non-string and blank entries never become issues."""
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "failed_checks": [],
                "parsing_errors": [None, 7, "   ", {"file": "x.tf"}],
            },
        },
    )

    assert_that(parse_checkov_output(report)).is_empty()


def test_parse_error_code_is_a_stable_literal() -> None:
    """The synthetic id is pinned to its literal, not to itself.

    Every other parse-error test asserts against the imported constant, so a
    rename would move production and tests together and stay green while
    breaking any downstream suppression keyed on the old id.
    """
    assert_that(CHECKOV_PARSE_ERROR_CODE).is_equal_to("CKV_PARSE_ERROR")


def test_report_block_without_a_results_mapping_is_skipped() -> None:
    """A block whose ``results`` is not a mapping yields nothing."""
    assert_that(parse_checkov_output(json.dumps({"results": "broken"}))).is_empty()


def test_non_dict_entries_in_a_list_report_are_skipped() -> None:
    """A multi-framework list carrying junk still yields the valid block."""
    payload = json.dumps(
        [
            "not-a-block",
            None,
            {"results": {"failed_checks": [_REAL_FAILED_CHECK]}},
        ],
    )

    assert_that(parse_checkov_output(payload)).is_length(1)


def test_failed_check_without_a_file_path_is_skipped() -> None:
    """A record missing its file is dropped rather than reported at ''."""
    broken = {**_REAL_FAILED_CHECK}
    broken.pop("file_abs_path")
    broken.pop("file_path")

    assert_that(parse_checkov_output(_report([broken]))).is_empty()


def test_message_falls_back_to_the_check_id_without_a_check_name() -> None:
    """An empty ``check_name`` still yields a message that names the failure.

    ``check_name`` is validated but not required, so a report missing it would
    otherwise render as a bare " (resource: …)" clause saying nothing about
    what failed.
    """
    nameless = {**_REAL_FAILED_CHECK, "check_name": ""}

    issues = parse_checkov_output(_report([nameless]))

    assert_that(issues).is_length(1)
    assert_that(issues[0].message).is_equal_to(
        "CKV_AWS_260 (resource: aws_security_group.allow_all)",
    )


def test_message_is_the_check_id_without_a_name_or_resource() -> None:
    """With neither name nor resource the id is the whole message."""
    bare = {**_REAL_FAILED_CHECK, "check_name": "", "resource": ""}

    issues = parse_checkov_output(_report([bare]))

    assert_that(issues[0].message).is_equal_to("CKV_AWS_260")
