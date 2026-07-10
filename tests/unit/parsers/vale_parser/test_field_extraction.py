"""Unit tests for Vale parser field extraction."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.vale.vale_parser import parse_vale_output

from .conftest import make_alert, make_output


def test_parse_real_output(real_vale_output: str) -> None:
    """Parser should extract all issues from real Vale output."""
    issues = parse_vale_output(output=real_vale_output)

    assert_that(len(issues)).is_equal_to(3)
    first = issues[0]
    assert_that(first.file).is_equal_to("vale_violations.md")
    assert_that(first.line).is_equal_to(3)
    assert_that(first.column).is_equal_to(1)
    assert_that(first.check).is_equal_to("Vale.Repetition")
    assert_that(first.style).is_equal_to("Vale")
    assert_that(first.severity).is_equal_to("error")
    assert_that(first.message).is_equal_to("'the' is repeated!")
    assert_that(first.match).is_equal_to("The the")


def test_column_derived_from_span() -> None:
    """The column should come from the first element of the Span array."""
    output = make_output(
        {"a.md": [make_alert(span=[37, 46])]},
    )

    issues = parse_vale_output(output=output)

    assert_that(issues[0].column).is_equal_to(37)


def test_style_extracted_from_check() -> None:
    """The style bundle should be the portion of Check before the dot."""
    output = make_output(
        {
            "a.md": [
                make_alert(check="Microsoft.Adverbs"),
                make_alert(check="Google.Passive"),
            ],
        },
    )

    issues = parse_vale_output(output=output)

    assert_that(issues[0].style).is_equal_to("Microsoft")
    assert_that(issues[0].check).is_equal_to("Microsoft.Adverbs")
    assert_that(issues[1].style).is_equal_to("Google")


def test_link_populates_doc_url() -> None:
    """A populated Link should map onto the issue doc_url."""
    output = make_output(
        {
            "a.md": [
                make_alert(link="https://docs.example.com/rule"),
            ],
        },
    )

    issues = parse_vale_output(output=output)

    assert_that(issues[0].doc_url).is_equal_to("https://docs.example.com/rule")


def test_missing_link_yields_empty_doc_url() -> None:
    """An empty Link should leave doc_url empty for later enrichment."""
    output = make_output({"a.md": [make_alert(link="")]})

    issues = parse_vale_output(output=output)

    assert_that(issues[0].doc_url).is_equal_to("")


def test_check_without_dot_uses_whole_string_as_style() -> None:
    """A check name without a dot should use the whole value as its style."""
    output = make_output({"a.md": [make_alert(check="Terms")]})

    issues = parse_vale_output(output=output)

    assert_that(issues[0].style).is_equal_to("Terms")
    assert_that(issues[0].check).is_equal_to("Terms")
