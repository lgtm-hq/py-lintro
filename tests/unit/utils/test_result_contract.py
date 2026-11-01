"""Output consumers require the canonical result's explicit success verdict."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.formatters.formatter import format_issues_with_sections
from lintro.models.core.tool_outcome import (
    Checked,
    FixUnmeasured,
    NoFiles,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.clippy.clippy_issue import ClippyIssue
from lintro.parsers.oxfmt.oxfmt_issue import OxfmtIssue
from lintro.parsers.prettier.prettier_issue import PrettierIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.sqlfluff.sqlfluff_issue import SqlfluffIssue
from lintro.parsers.taplo.taplo_parser import parse_taplo_output
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from lintro.utils.execution.exit_codes import determine_exit_code
from lintro.utils.execution.result_shaping import filter_result_to_fixable
from lintro.utils.findings import findings_from_results
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.summary_tables import count_fixable_issues, print_summary_table


@pytest.mark.parametrize(
    "outcome",
    [
        ToolOutcome(TimedOut("partial output"), issues_count=1),
        ToolOutcome(
            Checked(False),
            FixUnmeasured("incomplete report", initial=1),
            issues_count=1,
        ),
        ToolOutcome(
            TimedOut("partial output"),
            FixUnmeasured("incomplete report", initial=1),
            issues_count=1,
        ),
    ],
    ids=["timeout", "unmeasured", "timeout-unmeasured"],
)
def test_preview_preserves_failure_evidence(outcome: ToolOutcome) -> None:
    """Filtering findings preserves timeout and unknown-accounting failures.

    Args:
        outcome: Valid failure state carrying partial findings.
    """
    issue = RuffIssue(file="a.py", message="not fixable")
    result = ToolResult.from_outcome(outcome, name="ruff", issues=[issue])

    preview = filter_result_to_fixable(result)

    assert_that(preview.outcome.execution).is_equal_to(outcome.execution)
    assert_that(preview.outcome.accounting).is_equal_to(outcome.accounting)
    assert_that(preview.issues).is_empty()
    assert_that(preview.issues_count).is_zero()
    assert_that(determine_exit_code(Action.CHECK, [preview], 0, 0)).is_equal_to(1)
    assert_that(result.issues).is_equal_to([issue])
    assert_that(result.outcome).is_equal_to(outcome)


def test_preview_keeps_a_partial_parse_failure_failed() -> None:
    """A parsed diagnostic does not erase an incomplete report's failure."""
    result = ToolResult(
        name="ruff",
        success=False,
        parse_failures_count=1,
        issues=[RuffIssue(file="a.py", message="not fixable")],
        issues_count=1,
    )

    preview = filter_result_to_fixable(result)

    assert_that(preview.outcome.execution).is_equal_to(Checked(False))
    assert_that(preview.parse_failures_count).is_equal_to(1)
    assert_that(preview.issues_count).is_zero()
    assert_that(determine_exit_code(Action.CHECK, [preview], 0, 0)).is_equal_to(1)


@pytest.mark.parametrize("execution", [NoFiles(), Skipped("no configuration")])
def test_preview_does_not_promote_inactive_payloads(
    execution: NoFiles | Skipped,
) -> None:
    """An uncounted diagnostic payload cannot create findings on inactive tools.

    Args:
        execution: Successful non-event whose count must remain zero.
    """
    outcome = ToolOutcome(execution)
    result = ToolResult.from_outcome(
        outcome,
        name="ruff",
        issues=[RuffIssue(file="a.py", message="legacy payload", fixable=True)],
    )

    preview = filter_result_to_fixable(result)

    assert_that(preview.outcome).is_equal_to(outcome)
    assert_that(determine_exit_code(Action.CHECK, [preview], 0, 0)).is_zero()


@pytest.mark.parametrize(
    "consume",
    [
        lambda result: serialize_tool_result(result, action=Action.CHECK),
        lambda result: determine_exit_code(Action.CHECK, [result], 0, 0),
        lambda result: print_summary_table(
            ThreadSafeConsoleLogger().console_output,
            Action.CHECK,
            [result],
        ),
        lambda result: ThreadSafeConsoleLogger().print_execution_summary(
            Action.CHECK,
            [result],
        ),
        lambda result: ThreadSafeConsoleLogger().print_execution_summary(
            Action.FIX,
            [result],
        ),
    ],
    ids=["json", "exit-code", "summary-table", "console-check", "console-fix"],
)
def test_missing_success_cannot_be_reported_as_a_pass(
    consume: Callable[[ToolResult], object],
) -> None:
    """A malformed result must not acquire a successful verdict on read.

    Args:
        consume: Result consumer at the output or exit-code boundary.
    """
    fields = vars(ToolResult(name="incomplete")).copy()
    del fields["success"]
    malformed = cast(ToolResult, SimpleNamespace(**fields))

    assert_that(consume).raises(AttributeError).when_called_with(malformed)


@pytest.mark.parametrize(
    ("tool_name", "issue"),
    [
        (
            "taplo",
            parse_taplo_output(
                "error[invalid_value]: invalid value\n  --> a.toml:1:1",
            )[0],
        ),
        (
            "taplo",
            parse_taplo_output(
                "error[expected_table_array]: expected array\n  --> a.toml:1:1",
            )[0],
        ),
        (
            "taplo",
            parse_taplo_output(
                'ERROR the file is not properly formatted path="a.toml"',
            )[0],
        ),
        ("sqlfluff", SqlfluffIssue(file="a.sql", code="PRS", message="Parse error")),
        ("sqlfluff", SqlfluffIssue(file="a.sql", code="LT01", message="Spacing")),
        ("prettier", PrettierIssue(file="a.js", message="Would reformat")),
        ("oxfmt", OxfmtIssue(file="a.js", message="Would reformat")),
        ("clippy", ClippyIssue(file="a.rs", message="Lint diagnostic")),
    ],
)
def test_preview_fallback_preserves_public_fixability(
    tool_name: str,
    issue: BaseIssue,
) -> None:
    """Preview estimates must not change console, JSON or MCP fixability.

    Args:
        tool_name: Tool whose check findings lack a native fix signal.
        issue: Parsed syntax, schema, lint or formatting diagnostic.
    """
    result = ToolResult(name=tool_name, issues_count=1, issues=[issue])

    assert_that(count_fixable_issues([result])).is_zero()
    assert_that(issue.to_display_row()["fixable"]).is_empty()
    sections = format_issues_with_sections([issue], output_format="plain")
    assert_that(sections).contains("Not auto-fixable issues")
    assert_that(sections).does_not_contain("Auto-fixable issues")
    finding = findings_from_results(results=[result], action=Action.CHECK)[0]
    assert_that(finding.to_dict()["fixable"]).is_false()
    wire = serialize_tool_result(result, action=Action.CHECK)
    assert_that(wire["issues_count"]).is_equal_to(1)
    assert_that(wire["issues"][0]).does_not_contain_key("fixable")

    preview = filter_result_to_fixable(result)
    assert_that(preview.issues_count).is_equal_to(1)
    assert_that(preview.issues).is_equal_to([issue])
    assert_that(preview.success).is_true()
    assert_that(result.success).is_false()
    assert_that(issue.fixable).is_false()
