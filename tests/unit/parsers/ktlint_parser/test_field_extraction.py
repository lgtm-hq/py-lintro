"""Tests for ktlint parser field extraction."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.ktlint.ktlint_parser import parse_ktlint_output

from .conftest import (
    REAL_KTLINT_OUTPUT,
    make_error,
    make_file_entry,
    make_ktlint_output,
)


def test_parse_extracts_all_fields() -> None:
    """A single error maps every field onto the issue."""
    output = make_ktlint_output(
        [
            make_file_entry(
                file="src/Example.kt",
                errors=[
                    make_error(
                        line=3,
                        column=14,
                        message='Missing spacing around "="',
                        rule="standard:op-spacing",
                    ),
                ],
            ),
        ],
    )

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/Example.kt")
    assert_that(issues[0].line).is_equal_to(3)
    assert_that(issues[0].column).is_equal_to(14)
    assert_that(issues[0].message).is_equal_to('Missing spacing around "="')
    assert_that(issues[0].rule).is_equal_to("standard:op-spacing")


def test_parse_real_output_flattens_errors() -> None:
    """Real captured ktlint output yields one issue per error."""
    issues = parse_ktlint_output(REAL_KTLINT_OUTPUT)

    assert_that(issues).is_length(4)
    rules = [issue.rule for issue in issues]
    assert_that(rules).contains(
        "standard:filename",
        "standard:function-return-type-spacing",
        "standard:colon-spacing",
        "standard:op-spacing",
    )
    # Every issue is attributed to the same source file.
    assert_that({issue.file for issue in issues}).is_equal_to({"src/Example.kt"})


def test_parse_multiple_files() -> None:
    """Errors from multiple files are flattened while preserving file paths."""
    output = make_ktlint_output(
        [
            make_file_entry(file="a/First.kt", errors=[make_error(line=1)]),
            make_file_entry(file="b/Second.kt", errors=[make_error(line=9)]),
        ],
    )

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("a/First.kt")
    assert_that(issues[0].line).is_equal_to(1)
    assert_that(issues[1].file).is_equal_to("b/Second.kt")
    assert_that(issues[1].line).is_equal_to(9)


def test_parse_strips_leading_log_line() -> None:
    """A warn-level log line prepended to stdout does not break parsing."""
    log = (
        "10:00:00.000 [main] WARN com.pinterest.ktlint.cli.internal."
        "KtlintCommandLine -- Lint has found errors than can be "
        "autocorrected using 'ktlint --format'\n"
    )
    output = make_ktlint_output([make_file_entry()], log_prefix=log)

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].rule).is_equal_to("standard:colon-spacing")
