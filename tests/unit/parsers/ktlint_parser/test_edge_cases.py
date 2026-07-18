"""Tests for ktlint parser edge cases (Kotlin script, missing fields)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.ktlint.ktlint_parser import parse_ktlint_output

from .conftest import make_error, make_file_entry, make_ktlint_output


def test_parse_kotlin_script_file() -> None:
    """Kotlin Script (.kts) findings parse the same as .kt findings."""
    output = make_ktlint_output(
        [
            make_file_entry(
                file="build.gradle.kts",
                errors=[
                    make_error(
                        line=1,
                        column=6,
                        message='Missing spacing around "="',
                        rule="standard:op-spacing",
                    ),
                ],
            ),
        ],
    )

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("build.gradle.kts")
    assert_that(issues[0].rule).is_equal_to("standard:op-spacing")


def test_parse_file_with_no_errors() -> None:
    """A file entry with an empty ``errors`` list produces no issues."""
    output = make_ktlint_output([make_file_entry(errors=[])])

    assert_that(parse_ktlint_output(output)).is_empty()


def test_parse_missing_fields_use_defaults() -> None:
    """Missing numeric/string fields fall back to defaults."""
    output = '[{"file": "a.kt", "errors": [{"message": "boom"}]}]'

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)
    assert_that(issues[0].rule).is_equal_to("")
    assert_that(issues[0].message).is_equal_to("boom")


def test_parse_non_numeric_location_defaults_to_zero() -> None:
    """Non-numeric line/column values degrade gracefully to zero."""
    output = (
        '[{"file": "a.kt", "errors": '
        '[{"line": "x", "column": null, "rule": "standard:filename"}]}]'
    )

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)
    assert_that(issues[0].rule).is_equal_to("standard:filename")


def test_parse_experimental_ruleset() -> None:
    """Experimental ruleset ids are preserved verbatim."""
    output = make_ktlint_output(
        [
            make_file_entry(
                errors=[make_error(rule="experimental:something")],
            ),
        ],
    )

    issues = parse_ktlint_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].rule).is_equal_to("experimental:something")
