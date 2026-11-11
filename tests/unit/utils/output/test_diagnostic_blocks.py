"""Tests for how execution diagnostics are shown in console and Markdown output.

A tool's own stdout/stderr is shown verbatim when its run failed. It must not
be able to rewrite the Markdown report's markup, and a runaway diagnostic must
not bury the rest of the run; JSON keeps the full text (#2371).
"""

from __future__ import annotations

import re

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.execution.run_renderer import _display_fix_result
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.output.file_writer import render_markdown_report
from lintro.utils.output.text_blocks import (
    DIAGNOSTIC_HEAD_LINES,
    DIAGNOSTIC_TAIL_LINES,
)

HOSTILE_DIAGNOSTIC = (
    "black failed (exit 123) running:\n"
    "```\n"
    "| injected | table |\n"
    "|---|---|\n"
    '<img src="x" onerror="alert(1)">\n'
    "``````\n"
    "error: cannot format a.py"
)


def _failed_result(output: str, *, issues: list[BaseIssue] | None = None) -> ToolResult:
    """Build a black result that failed to run, as the black plugin does.

    Args:
        output: Diagnostic text the tool printed.
        issues: Findings recovered before the failure, if any.

    Returns:
        A result flagged as an execution failure.
    """
    found = issues or []
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=False, inspected_files=None),
            issues_count=len(found),
        ),
        name="black",
        findings_complete=False,
        execution_failed=True,
        output=output,
        issues=found,
    )


def _fenced_bodies(markdown: str) -> list[list[str]]:
    """Return the lines inside each fenced code block of a Markdown document.

    Args:
        markdown: Rendered Markdown.

    Returns:
        One list of body lines per fenced block, in document order.
    """
    lines = markdown.splitlines()
    bodies: list[list[str]] = []
    index = 0
    while index < len(lines):
        opening = re.match(r"^(`{3,})", lines[index])
        if opening is None:
            index += 1
            continue
        fence = opening.group(1)
        # CommonMark: the block closes at the first line of at least as many
        # backticks and nothing else.
        close = next(
            i
            for i in range(index + 1, len(lines))
            if re.fullmatch(rf"`{{{len(fence)},}}", lines[i].strip())
        )
        bodies.append(lines[index + 1 : close])
        index = close + 1
    return bodies


def _console(result: ToolResult) -> str:
    """Render a result through the per-tool console path.

    Args:
        result: Result to render.

    Returns:
        Everything printed for it, joined by newlines.
    """
    printed: list[str] = []

    def emit(text: str = "", **_: object) -> None:
        printed.append(text)

    def emit_success(message: str = "", **_: object) -> None:
        printed.append(message)

    _display_fix_result(
        result,
        output_format="grid",
        raw_output=False,
        console_output_func=emit,
        success_func=emit_success,
        action=Action.CHECK,
    )
    return "\n".join(printed)


def test_diagnostic_cannot_break_out_of_its_markdown_block() -> None:
    """Fences, table rows and HTML in a diagnostic all stay inside one block."""
    report = render_markdown_report(
        [_failed_result(HOSTILE_DIAGNOSTIC)],
        action=Action.CHECK,
    )

    bodies = _fenced_bodies(report)
    assert_that(bodies).is_length(1)
    assert_that("\n".join(bodies[0])).is_equal_to(HOSTILE_DIAGNOSTIC)
    outside = report.split(bodies[0][0])[0]
    assert_that(outside).does_not_contain("<img", "| injected |")


def test_fix_mode_tool_output_cannot_break_out_of_its_block() -> None:
    """The fix-mode "Tool output" block uses the same safe fence."""
    issue = BaseIssue(file="a.py", line=1, message="would reformat")
    result = ToolResult(
        name="black",
        success=True,
        issues_count=0,
        output=HOSTILE_DIAGNOSTIC,
        initial_issues=[issue],
        issues=[],
    )

    report = render_markdown_report([result], action=Action.FIX)

    bodies = _fenced_bodies(report)
    assert_that(bodies).is_length(1)
    assert_that("\n".join(bodies[0])).is_equal_to(HOSTILE_DIAGNOSTIC)


@pytest.fixture
def long_diagnostic() -> str:
    """Provide a 5,000-line diagnostic with a labelled head and an error tail.

    Returns:
        The diagnostic text.
    """
    body = [f"  frame {number}" for number in range(4_997)]
    return "\n".join(
        [
            "black failed (exit 123) running:",
            "  black --check .",
            *body,
            "RecursionError: maximum recursion depth exceeded",
        ],
    )


def test_console_bounds_a_long_diagnostic(long_diagnostic: str) -> None:
    """The console keeps the head and tail and says how much it left out.

    Args:
        long_diagnostic: A 5,000-line diagnostic.
    """
    text = _console(_failed_result(long_diagnostic))

    omitted = 5_000 - DIAGNOSTIC_HEAD_LINES - DIAGNOSTIC_TAIL_LINES
    assert_that(text).contains(
        "black failed (exit 123) running:",
        "  black --check .",
        f"... ({omitted} lines omitted by lintro; the full output is in the "
        "JSON report: --output-format json)",
        "RecursionError: maximum recursion depth exceeded",
    )
    assert_that(text).does_not_contain("  frame 2500\n")
    assert_that(len(text.splitlines())).is_less_than(100)


def test_console_bounds_a_diagnostic_beside_recovered_issues(
    long_diagnostic: str,
) -> None:
    """Recovered issues still print, followed by the bounded diagnostic.

    Args:
        long_diagnostic: A 5,000-line diagnostic.
    """
    issue = BaseIssue(file="a.py", line=3, message="recovered before the crash")

    text = _console(_failed_result(long_diagnostic, issues=[issue]))

    assert_that(text).contains(
        "recovered before the crash",
        "lines omitted by lintro",
        "RecursionError: maximum recursion depth exceeded",
    )
    assert_that(len(text.splitlines())).is_less_than(120)


def test_markdown_bounds_a_long_diagnostic_and_json_keeps_it(
    long_diagnostic: str,
) -> None:
    """Markdown shows the bounded text; the JSON report keeps every line.

    Args:
        long_diagnostic: A 5,000-line diagnostic.
    """
    result = _failed_result(long_diagnostic)

    report = render_markdown_report([result], action=Action.CHECK)
    payload = serialize_tool_result(result, action=Action.CHECK)

    assert_that(len(report.splitlines())).is_less_than(100)
    assert_that(report).contains(
        "lines omitted by lintro",
        "RecursionError: maximum recursion depth exceeded",
    )
    assert_that(payload["output"]).is_equal_to(long_diagnostic)


def test_short_diagnostic_is_shown_whole() -> None:
    """A diagnostic within the limit is printed unchanged, with no notice."""
    diagnostic = "\n".join(
        f"line {number}"
        for number in range(DIAGNOSTIC_HEAD_LINES + DIAGNOSTIC_TAIL_LINES)
    )

    text = _console(_failed_result(diagnostic))

    assert_that(text).contains(diagnostic)
    assert_that(text).does_not_contain("omitted by lintro")
