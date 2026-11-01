"""Primary report containers distinguish an empty scan from an invalid report."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import NamedTuple

import pytest
from assertpy import assert_that

from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.checkov import checkov_parser
from lintro.parsers.checkov.checkov_parser import parse_checkov_output_with_stats
from lintro.parsers.golangci_lint import golangci_lint_parser
from lintro.parsers.html_validate import html_validate_parser
from lintro.parsers.ruff import ruff_parser
from lintro.parsers.sqlfluff import sqlfluff_parser
from lintro.parsers.stylelint import stylelint_parser
from lintro.parsers.vale import vale_parser

PARSER_MODULES = {
    "checkov": checkov_parser,
    "golangci_lint": golangci_lint_parser,
    "html_validate": html_validate_parser,
    "ruff": ruff_parser,
    "sqlfluff": sqlfluff_parser,
    "stylelint": stylelint_parser,
    "vale": vale_parser,
}
INVALID_LIST_CONTAINERS: tuple[object, ...] = (None, {}, False, "invalid")
INVALID_RESULT_CONTAINERS: tuple[object, ...] = (None, [], False, 42)


class ReportCase(NamedTuple):
    """A tool's parser entry points and representative report envelopes."""

    parse: Callable[[str], tuple[list[BaseIssue], int]]
    legacy: Callable[[str], list[BaseIssue]]
    invalid: list[object]
    empty: list[object]
    mixed: object


@pytest.fixture(
    params=[
        "checkov",
        "golangci_lint",
        "html_validate",
        "ruff",
        "sqlfluff",
        "stylelint",
        "vale",
    ],
)
def report_case(request: pytest.FixtureRequest) -> ReportCase:
    """Provide schema-specific invalid, empty and partially usable reports.

    Args:
        request: Tool name selected by pytest.

    Returns:
        Parser functions and JSON values for that tool's actual schema.
    """
    name: str = request.param
    invalid: list[object] = [None, False, 42, "invalid"]
    if name == "checkov":
        invalid += [{}]
        invalid += [{"results": value} for value in INVALID_RESULT_CONTAINERS]
        invalid += [{"results": {}}]
        invalid += [
            {"results": {"failed_checks": value}} for value in INVALID_LIST_CONTAINERS
        ]
        empty: list[object] = [[], {"results": {"failed_checks": []}}]
        checks = [
            {"check_id": f"CKV_{i}", "file_path": "a.tf", "check_name": message}
            for i, message in enumerate(("first", "second"))
        ]
        mixed: object = [
            {"results": {"failed_checks": checks}},
            {"results": {"failed_checks": None}},
        ]
    elif name == "golangci_lint":
        invalid += [[], {}, {"Issues": None}, {"Issues": {}}, {"Issues": False}]
        empty = [{"Issues": []}]
        mixed = {
            "Issues": [
                {"Text": "first", "Pos": {"Filename": "a.go"}},
                None,
                {"Text": "second", "Pos": {"Filename": "b.go"}},
            ],
        }
    elif name == "ruff":
        invalid += [{}]
        empty = [[]]
        mixed = [
            {"filename": "a.py", "message": "first"},
            None,
            {"filename": "b.py", "message": "second"},
        ]
    elif name == "vale":
        invalid += [[], {"a.md": None}, {"a.md": {}}, {"a.md": False}]
        empty = [{}, {"a.md": []}]
        mixed = {
            "a.md": [{"Message": "first"}, {"Message": "second"}],
            "b.md": None,
        }
    else:
        path_key, list_key, text_key = {
            "html_validate": ("filePath", "messages", "message"),
            "sqlfluff": ("filepath", "violations", "description"),
            "stylelint": ("source", "warnings", "text"),
        }[name]
        invalid += [{}, [{path_key: "a"}]]
        invalid += [
            [{path_key: "a", list_key: value}] for value in INVALID_LIST_CONTAINERS
        ]
        empty = [[], [{path_key: "a", list_key: []}]]
        mixed = [
            {path_key: "a", list_key: [{text_key: "first"}]},
            {path_key: "b", list_key: None},
            {path_key: "c", list_key: [{text_key: "second"}]},
        ]
    module = PARSER_MODULES[name]
    return ReportCase(
        parse=getattr(module, f"parse_{name}_output_with_stats"),
        legacy=getattr(module, f"parse_{name}_output"),
        invalid=invalid,
        empty=empty,
        mixed=mixed,
    )


def test_invalid_primary_containers_count_as_failures(report_case: ReportCase) -> None:
    """Null, absent and wrong-type containers cannot be mistaken for no findings."""
    for value in report_case.invalid:
        payload = json.dumps(value)
        issues, failures = report_case.parse(payload)

        assert_that(issues).described_as(payload).is_empty()
        assert_that(failures).described_as(payload).is_positive()


def test_valid_empty_containers_remain_clean(report_case: ReportCase) -> None:
    """Empty arrays and mappings are accepted only in each tool's valid schema."""
    for value in report_case.empty:
        payload = json.dumps(value)

        assert_that(report_case.parse(payload)).is_equal_to(([], 0))
        assert_that(report_case.legacy(payload)).is_empty()


def test_mixed_reports_keep_all_valid_findings(report_case: ReportCase) -> None:
    """A bad neighbor counts once without erasing either valid finding."""
    payload = json.dumps(report_case.mixed)
    issues, failures = report_case.parse(payload)

    assert_that(failures).is_equal_to(1)
    assert_that([issue.message for issue in issues]).is_equal_to(["first", "second"])
    assert_that(report_case.legacy(payload)).is_equal_to(issues)


def test_checkov_file_parsing_error_survives_invalid_failed_checks() -> None:
    """The policy container cannot hide Checkov's independent unreadable-file issue."""
    issues, failures = parse_checkov_output_with_stats(
        '{"results": {"failed_checks": null, "parsing_errors": ["broken.tf"]}}',
    )

    assert_that(failures).is_equal_to(1)
    assert_that([(issue.file, issue.check_id) for issue in issues]).is_equal_to(
        [("broken.tf", "CKV_PARSE_ERROR")],
    )


@pytest.mark.parametrize("invalid_count", [None, False, 1, "0"])
def test_checkov_no_work_summary_requires_explicit_zero_counts(
    invalid_count: object,
) -> None:
    """A partial or inconsistent summary does not qualify as Checkov's no-work form."""
    summary: dict[str, object] = dict.fromkeys(
        ["passed", "failed", "skipped", "parsing_errors", "resource_count"],
        0,
    )
    summary.update(checkov_version="3.3.29", failed=invalid_count)
    issues, failures = parse_checkov_output_with_stats(json.dumps(summary))

    assert_that(issues).is_empty()
    assert_that(failures).is_equal_to(1)


@pytest.mark.parametrize(
    "output",
    ['notice\n[{"other": []}]', 'notice\n{"warnings": []}'],
)
def test_stylelint_nested_empty_array_is_not_a_report(output: str) -> None:
    """Noise extraction must validate the outer report rather than a nested list."""
    issues, failures = stylelint_parser.parse_stylelint_output_with_stats(output)

    assert_that(issues).is_empty()
    assert_that(failures).is_equal_to(1)
