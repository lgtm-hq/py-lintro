"""Unit tests for format_tool_output function.

This module tests the output formatting functions for transforming
tool outputs into various display formats.
"""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.utils.output.file_writer import (
    TABULATE_AVAILABLE,
    format_tool_output,
)

# =============================================================================
# Tests for format_tool_output - Empty/whitespace handling
# =============================================================================


@pytest.mark.parametrize(
    ("raw_output", "expected"),
    [
        ("", "No issues found."),
        ("   ", "No issues found."),
        ("\n\t  ", "No issues found."),
        ("  \n  \t  \n  ", "No issues found."),
    ],
    ids=["empty", "spaces", "whitespace-mix", "multiline-whitespace"],
)
def test_format_tool_output_returns_no_issues_for_empty_input(
    raw_output: str,
    expected: str,
) -> None:
    """Verify empty or whitespace-only output returns 'No issues found' message.

    Args:
        raw_output: Description of raw_output (str).
        expected: Description of expected (str).
    """
    result = format_tool_output("ruff", raw_output)
    assert_that(result).is_equal_to(expected)


# =============================================================================
# Tests for format_tool_output - Issue formatting
# =============================================================================


def test_format_tool_output_formats_provided_issues() -> None:
    """Verify provided issues are formatted using the issue formatter.

    When real issue objects with to_display_row are provided, the formatter
    should process them and return formatted output with file and line info.
    """
    from lintro.parsers.ruff.ruff_issue import RuffIssue

    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="E001",
            message="Error message",
            fixable=False,
        ),
    ]

    result = format_tool_output("ruff", "", issues=issues)

    assert_that(result).is_instance_of(str)
    assert_that(result).is_not_empty()
    # The formatted output should contain the issue data
    assert_that(result).contains("src/main.py")
    assert_that(result).contains("E001")


def test_format_tool_output_falls_back_to_raw_for_unknown_tool() -> None:
    """Verify unrecognized tool output is returned as-is when parsing fails."""
    raw_output = "Unknown format that can't be parsed"

    result = format_tool_output("unknown-tool", raw_output)

    assert_that(result).is_equal_to(raw_output)


# =============================================================================
# Tests for format_tool_output - Tool-specific parsing
# =============================================================================


@pytest.mark.parametrize(
    ("tool_name", "raw_output", "expected_content"),
    [
        (
            "ruff",
            "src/main.py:10:5: E001 Error message",
            "src/main.py",
        ),
        (
            "mypy",
            "src/main.py:10: error: Type error [type-error]",
            "src/main.py",
        ),
        (
            "black",
            "would reformat src/main.py",
            "src/main.py",
        ),
        (
            "hadolint",
            "Dockerfile:10 DL3006 warning: Always tag the version",
            "Dockerfile",
        ),
        (
            "actionlint",
            ".github/workflows/ci.yml:10:1: error: Syntax error [syntax]",
            ".github/workflows/ci.yml",
        ),
        (
            "pydoclint",
            "src/main.py:10:1: DOC101 Missing docstring",
            "src/main.py",
        ),
        (
            "markdownlint",
            "README.md:10: MD013 Line too long",
            "README.md",
        ),
        (
            "clippy",
            "warning: unused variable\n --> src/main.rs:10:5",
            "src/main.rs",
        ),
        (
            "pytest",
            "FAILED tests/test_main.py::test_func - AssertionError",
            "tests/test_main.py",
        ),
        (
            "yamllint",
            "config.yml\n  10:1       warning  trailing spaces  (trailing-spaces)",
            "config.yml",
        ),
    ],
    ids=[
        "ruff",
        "mypy",
        "black",
        "hadolint",
        "actionlint",
        "pydoclint",
        "markdownlint",
        "clippy",
        "pytest",
        "yamllint",
    ],
)
def test_format_tool_output_parses_tool_specific_formats(
    tool_name: str,
    raw_output: str,
    expected_content: str,
) -> None:
    """Verify tool-specific output is parsed and formatted with file references.

    Each tool has its own output format. The parser should extract meaningful
    information like file paths and include them in the formatted result.

    Args:
        tool_name: Name of the linting tool.
        raw_output: Raw output from the tool.
        expected_content: Expected content to be found in the formatted output.
    """
    result = format_tool_output(tool_name, raw_output, output_format="plain")

    assert_that(result).is_instance_of(str)
    assert_that(result).is_not_empty()
    # The result should contain the file reference from the parsed output
    assert_that(result).contains(expected_content)


def test_format_tool_output_parses_bandit_json() -> None:
    """Verify bandit JSON output is parsed with security issue details."""
    bandit_output = json.dumps(
        {
            "results": [
                {
                    "filename": "src/main.py",
                    "line_number": 10,
                    "test_id": "B101",
                    "issue_text": "Security issue",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                },
            ],
        },
    )

    result = format_tool_output("bandit", bandit_output, output_format="plain")

    assert_that(result).is_instance_of(str)
    assert_that(result).is_not_empty()
    assert_that(result).contains("src/main.py")
    assert_that(result).contains("B101")


def test_format_tool_output_handles_invalid_bandit_json() -> None:
    """Verify invalid bandit JSON returns error with raw output for debugging."""
    bandit_output = "not valid json { broken"

    result = format_tool_output("bandit", bandit_output, output_format="plain")

    # Should return error message with raw output for debugging
    assert_that(result).contains("Error:")
    assert_that(result).contains("Failed to parse Bandit output")
    assert_that(result).contains("Raw output:")
    assert_that(result).contains(bandit_output)


def test_format_tool_output_skips_reparse_for_clean_pass() -> None:
    """Verify informational output on a clean pass is not routed through parser.

    A successful, zero-issue run whose ``output`` is a non-JSON informational
    string must be returned verbatim instead of raising a parse error (#1534).
    """
    informational = "No .py/.pyi files found to check."

    result = format_tool_output(
        "bandit",
        informational,
        output_format="plain",
        success=True,
        issues_count=0,
    )

    assert_that(result).is_equal_to(informational)
    assert_that(result).does_not_contain("Error:")


def test_format_tool_output_still_errors_on_failed_bandit_run() -> None:
    """Verify a failed bandit run with unparseable output still surfaces an error.

    The clean-pass skip must not weaken the #1044 guarantee: real unparseable
    bandit output from a failed run must still be reported as an error.
    """
    bandit_output = "not valid json { broken"

    result = format_tool_output(
        "bandit",
        bandit_output,
        output_format="plain",
        success=False,
        issues_count=0,
    )

    assert_that(result).contains("Error:")
    assert_that(result).contains("Failed to parse Bandit output")


@pytest.mark.parametrize(
    ("output_format_str",),
    [
        ("grid",),
        ("plain",),
        ("GRID",),
        ("Plain",),
    ],
    ids=["grid-lower", "plain-lower", "grid-upper", "plain-mixed"],
)
def test_format_tool_output_normalizes_format_string(output_format_str: str) -> None:
    """Verify output format strings are normalized case-insensitively.

    Args:
        output_format_str: Output format string to normalize.
    """
    result = format_tool_output("ruff", "", output_format=output_format_str)

    assert_that(result).is_equal_to("No issues found.")


# =============================================================================
# Tests for tabulate availability
# =============================================================================


def test_tabulate_available_flag_is_true_in_test_environment() -> None:
    """Verify tabulate library is available and flag is set correctly.

    The test environment should have tabulate installed, so this flag
    should be True, enabling table formatting features.
    """
    assert_that(TABULATE_AVAILABLE).is_instance_of(bool)
    assert_that(TABULATE_AVAILABLE).is_true()
