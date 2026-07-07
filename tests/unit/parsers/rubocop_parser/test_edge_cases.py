"""Edge-case tests for the RuboCop parser."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output

from .conftest import make_offense, make_rubocop_output


def test_cop_without_department() -> None:
    """A cop name without a slash yields an empty department."""
    output = make_rubocop_output({"a.rb": [make_offense(cop_name="CustomCop")]})
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("CustomCop")
    assert_that(issues[0].department).is_equal_to("")


def test_extension_department_cops() -> None:
    """Extension cops (Rails, RSpec, Performance) keep their department."""
    output = make_rubocop_output(
        {
            "app.rb": [
                make_offense(cop_name="Rails/TimeZone"),
                make_offense(cop_name="RSpec/ExampleLength"),
                make_offense(cop_name="Performance/Detect"),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that([i.department for i in issues]).is_equal_to(
        ["Rails", "RSpec", "Performance"],
    )


def test_missing_location_defaults_to_zero() -> None:
    """An offense without a location falls back to line/column 0."""
    output = (
        '{"files": [{"path": "a.rb", "offenses": '
        '[{"cop_name": "Lint/Syntax", "severity": "fatal", "message": "boom", '
        '"correctable": false}]}]}'
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)
    assert_that(issues[0].severity).is_equal_to("fatal")


def test_corrected_offense_flag() -> None:
    """A corrected offense preserves both correctable and corrected flags."""
    output = make_rubocop_output(
        {
            "a.rb": [
                make_offense(
                    cop_name="Style/StringLiterals",
                    correctable=True,
                    corrected=True,
                ),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].correctable).is_true()
    assert_that(issues[0].corrected).is_true()


def test_multi_line_offense_range() -> None:
    """A multi-line offense captures distinct start and end positions."""
    output = make_rubocop_output(
        {
            "a.rb": [
                make_offense(
                    cop_name="Layout/IndentationConsistency",
                    start_line=4,
                    start_column=3,
                    last_line=5,
                    last_column=8,
                ),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(4)
    assert_that(issues[0].column).is_equal_to(3)
    assert_that(issues[0].end_line).is_equal_to(5)
    assert_that(issues[0].end_column).is_equal_to(8)
