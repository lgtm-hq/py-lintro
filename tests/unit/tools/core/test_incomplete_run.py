"""Tests for results of tool runs without a complete verdict (#3352)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.vale.vale_issue import ValeIssue
from lintro.tools.core.incomplete_run import (
    MAX_MESSAGE_LINE_LENGTH,
    incomplete_run_result,
    report_gap,
    tool_message_excerpt,
)


def test_excerpt_quotes_first_lines_and_cuts_long_ones() -> None:
    """A stack trace is cut to its first lines, and one-line JSON is capped."""
    trace = "\n".join(
        [
            "SyntaxError: JSON Error in .stylelintrc.json:",
            "",
            "Unexpected end of JSON input",
            "    at JSON.parse (<anonymous>)",
            "    at loadJson (loaders.js:37:21)",
        ],
    )
    long_line = "[" + "x" * (MAX_MESSAGE_LINE_LENGTH * 2) + "]"

    assert_that(tool_message_excerpt(trace)).is_equal_to(
        "SyntaxError: JSON Error in .stylelintrc.json: | "
        "Unexpected end of JSON input | at JSON.parse (<anonymous>)",
    )
    assert_that(tool_message_excerpt(long_line)).is_length(
        MAX_MESSAGE_LINE_LENGTH + 3,
    ).ends_with("...")
    assert_that(tool_message_excerpt("  \n")).is_equal_to("no output")


@pytest.mark.parametrize(
    ("output", "run_success", "found", "failures", "expected"),
    [
        pytest.param("", True, False, 0, ("Tool printed no report.", None), id="empty"),
        pytest.param(
            " \n",
            False,
            False,
            0,
            ("Tool exited with an error and printed no report.", None),
            id="empty-failed-exit",
        ),
        pytest.param(
            "Error: boom",
            False,
            False,
            1,
            ("Tool exited with an error and reported no findings: Error: boom", None),
            id="text-failed-exit-is-not-malformed",
        ),
        pytest.param("[x]", True, True, 0, None, id="complete-with-findings"),
        pytest.param("[]", True, False, 0, None, id="complete-clean"),
    ],
)
def test_report_gap_separates_execution_failure_from_complete_report(
    output: str,
    run_success: bool,
    found: bool,
    failures: int,
    expected: tuple[str, int | None] | None,
) -> None:
    """No report or a failed exit with nothing parsed is an execution failure."""
    gap = report_gap(
        tool="Tool",
        output=output,
        run_success=run_success,
        found_issues=found,
        parse_failures=failures,
    )

    assert_that(gap).is_equal_to(expected)


def test_report_gap_counts_malformed_records_in_a_report() -> None:
    """Malformed records beside a recognised verdict are a malformed report."""
    gap = report_gap(
        tool="Tool",
        output='{"a.md": "not a list"}',
        run_success=False,
        found_issues=True,
        parse_failures=2,
    )

    assert_that(gap).is_not_none()
    reason, failures = gap or ("", None)
    assert_that(failures).is_equal_to(2)
    assert_that(reason).contains("could not be fully parsed", "2 malformed")


def test_incomplete_result_keeps_real_findings_and_claims_nothing() -> None:
    """Real findings survive; inspection is unknown and nothing is synthetic."""
    finding = ValeIssue(file="a.md", line=3, message="'is' is repeated!")

    result = incomplete_run_result(
        name="vale",
        reason="Vale printed a report that could not be fully parsed.",
        issues=[finding],
        parse_failures=1,
        cwd="/repo",
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(list(result.issues)).is_equal_to([finding])
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.cwd).is_equal_to("/repo")
