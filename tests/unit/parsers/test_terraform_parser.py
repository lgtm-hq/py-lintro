"""Unit tests for the terraform parser."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.terraform.terraform_parser import (
    parse_terraform_fmt_output,
    parse_terraform_output,
    parse_terraform_validate_output,
)


@pytest.mark.parametrize(
    "output",
    [None, "", "   \n  \n   "],
    ids=["none", "empty", "whitespace_only"],
)
def test_parse_fmt_output_empty(output: str | None) -> None:
    """Empty, None, or whitespace-only fmt output yields no issues.

    Args:
        output: The fmt output to parse.
    """
    assert_that(parse_terraform_fmt_output(output)).is_empty()


def test_parse_fmt_output_lists_offending_files() -> None:
    """Each Terraform file path on its own line becomes a formatting issue."""
    output = "main.tf\nmodules/network/variables.tf\nterraform.tfvars\n"
    issues = parse_terraform_fmt_output(output)

    assert_that(issues).is_length(3)
    assert_that([i.file for i in issues]).is_equal_to(
        ["main.tf", "modules/network/variables.tf", "terraform.tfvars"],
    )
    assert_that(issues[0].code).is_equal_to("fmt")
    assert_that(issues[0].level).is_equal_to("error")
    assert_that(issues[0].line).is_equal_to(0)


def test_parse_fmt_output_ignores_non_paths() -> None:
    """Non-path noise lines are ignored by the fmt parser."""
    output = "Error: something went wrong\nmain.tf\n\n  \n"
    issues = parse_terraform_fmt_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("main.tf")


def test_parse_fmt_output_applies_base_dir() -> None:
    """A base_dir prefix is prepended to reported file paths."""
    issues = parse_terraform_fmt_output("main.tf\n", base_dir="infra")

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("infra/main.tf")


@pytest.mark.parametrize(
    "output",
    [None, "", "   ", "not json at all"],
    ids=["none", "empty", "whitespace", "invalid_json"],
)
def test_parse_validate_output_empty_or_invalid(output: str | None) -> None:
    """Empty or non-JSON validate output yields no issues.

    Args:
        output: The validate output to parse.
    """
    assert_that(parse_terraform_validate_output(output)).is_empty()


def test_parse_validate_output_extracts_all_fields() -> None:
    """A validate diagnostic maps to an issue with all fields populated."""
    payload = {
        "format_version": "1.0",
        "valid": False,
        "error_count": 1,
        "warning_count": 0,
        "diagnostics": [
            {
                "severity": "error",
                "summary": "Reference to undeclared local value",
                "detail": 'A local value with the name "x" has not been declared.',
                "range": {
                    "filename": "main.tf",
                    "start": {"line": 7, "column": 11, "byte": 90},
                    "end": {"line": 7, "column": 31, "byte": 110},
                },
            },
        ],
    }
    issues = parse_terraform_validate_output(json.dumps(payload), module_dir="infra")

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("infra/main.tf")
    assert_that(issue.line).is_equal_to(7)
    assert_that(issue.column).is_equal_to(11)
    assert_that(issue.level).is_equal_to("error")
    assert_that(issue.code).is_equal_to("validate")
    assert_that(issue.message).contains("Reference to undeclared local value")
    assert_that(issue.message).contains("has not been declared")


def test_parse_validate_output_warning_without_range() -> None:
    """A warning without a range falls back to the module directory as file."""
    payload = {
        "diagnostics": [
            {
                "severity": "warning",
                "summary": "Deprecated attribute",
                "detail": "",
            },
        ],
    }
    issues = parse_terraform_validate_output(json.dumps(payload), module_dir="mod")

    assert_that(issues).is_length(1)
    assert_that(issues[0].level).is_equal_to("warning")
    assert_that(issues[0].file).is_equal_to("mod")
    assert_that(issues[0].message).is_equal_to("Deprecated attribute")


def test_parse_validate_output_no_diagnostics() -> None:
    """A valid config with an empty diagnostics array yields no issues."""
    payload = {"valid": True, "diagnostics": []}
    assert_that(
        parse_terraform_validate_output(json.dumps(payload)),
    ).is_empty()


def test_parse_terraform_output_dispatches_json_to_validate() -> None:
    """A JSON payload is routed to the validate parser."""
    payload = {
        "diagnostics": [
            {"severity": "error", "summary": "boom", "detail": "bad"},
        ],
    }
    issues = parse_terraform_output(json.dumps(payload))

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("validate")


def test_parse_terraform_output_dispatches_text_to_fmt() -> None:
    """Non-JSON output is routed to the fmt parser."""
    issues = parse_terraform_output("main.tf\n")

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("fmt")


@pytest.mark.parametrize(
    "output",
    [None, "", "   "],
    ids=["none", "empty", "whitespace"],
)
def test_parse_terraform_output_empty(output: str | None) -> None:
    """Empty dispatcher input yields no issues.

    Args:
        output: The output to parse.
    """
    assert_that(parse_terraform_output(output)).is_empty()
