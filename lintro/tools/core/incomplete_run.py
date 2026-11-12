"""Results for tool runs that ended without a complete verdict (#3352).

A run that could not execute, printed no report, or printed a report lintro
could not fully parse proves neither a clean pass nor which files were read.
Such a run is an execution failure: ``execution_failed`` is set, findings are
incomplete, inspection stays unknown so the run can never update a severity
baseline, and only the findings the tool really reported are kept. Nothing
synthetic is added for the failure itself, and the output names the reason
instead of echoing the tool's raw report.
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue

#: Lines of a tool's own message quoted in a failure reason.
MAX_MESSAGE_LINES: int = 3

#: Characters of each quoted line, so one-line JSON cannot flood the output.
MAX_MESSAGE_LINE_LENGTH: int = 200


_TRACEBACK_HEADER: str = "Traceback (most recent call last):"


def _after_python_traceback(lines: list[str]) -> list[str]:
    """Drop the stack frames of the last Python traceback in ``lines``.

    Args:
        lines: A tool's output, one entry per line.

    Returns:
        The lines from the exception line on, or ``lines`` unchanged when they
        hold no traceback or nothing follows its frames.
    """
    headers = [i for i, line in enumerate(lines) if line.strip() == _TRACEBACK_HEADER]
    if not headers:
        return lines
    rest = lines[headers[-1] + 1 :]
    # Frames and their source lines are indented; the exception is not.
    for index, line in enumerate(rest):
        if line.strip() and not line[0].isspace():
            return rest[index:]
    return lines


def tool_message_excerpt(text: str | None) -> str:
    """Quote the first lines of what a tool printed when it failed.

    Args:
        text: The tool's stderr, or its combined output.

    Returns:
        Up to ``MAX_MESSAGE_LINES`` non-empty lines joined by `` | ``, each cut
        to ``MAX_MESSAGE_LINE_LENGTH`` characters, or ``"no output"``. For a
        Python traceback the quote starts at the exception line, since its
        first lines are only stack frames.
    """
    raw = _after_python_traceback((text or "").splitlines())
    lines = [line.strip() for line in raw if line.strip()]
    if not lines:
        return "no output"
    quoted = [
        (
            line
            if len(line) <= MAX_MESSAGE_LINE_LENGTH
            else f"{line[:MAX_MESSAGE_LINE_LENGTH]}..."
        )
        for line in lines[:MAX_MESSAGE_LINES]
    ]
    return " | ".join(quoted)


def malformed_report_reason(tool: str, failures: int) -> str:
    """Name a report that was printed but could not be fully parsed.

    Args:
        tool: Display name of the tool.
        failures: Malformed report containers or records.

    Returns:
        A one-line reason naming the count.
    """
    return (
        f"{tool} printed a report that could not be fully parsed "
        f"({failures} malformed record(s) or container(s)); "
        "its findings are incomplete."
    )


def incomplete_run_result(
    *,
    name: str,
    reason: str,
    issues: Sequence[BaseIssue] = (),
    parse_failures: int | None = None,
    cwd: str | None = None,
) -> ToolResult:
    """Build the result for a run that produced no complete verdict.

    Args:
        name: Tool name for the result.
        reason: Named reason the verdict is incomplete.
        issues: Findings the tool really reported, kept as they are.
        parse_failures: Malformed report containers or records, or None when
            no report was printed at all.
        cwd: Working directory the tool ran in.

    Returns:
        A failed ToolResult flagged as an execution failure with unknown
        inspection.
    """
    kept = list(issues)
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=False, inspected_files=None),
            issues_count=len(kept),
        ),
        name=name,
        findings_complete=False,
        execution_failed=True,
        output=reason,
        issues=kept,
        parse_failures_count=parse_failures,
        cwd=cwd,
    )


def report_gap(
    *,
    tool: str,
    output: str | None,
    run_success: bool,
    found_issues: bool,
    parse_failures: int,
) -> tuple[str, int | None] | None:
    """Name why a run whose tool always prints a report has no verdict.

    For tools that print a report on every completed run, even a clean one,
    empty output means the tool did not run, and a failed exit with nothing
    parsed is an execution or configuration error, whatever the text says:
    neither is a malformed report. Only a report with malformed records at
    a recognised verdict counts its parse failures.

    Args:
        tool: Display name of the tool.
        output: What the tool printed.
        run_success: Whether the tool exited 0.
        found_issues: Whether any finding was parsed.
        parse_failures: Malformed report containers or records.

    Returns:
        The reason and the parse-failure count to report (None when no report
        was printed), or None when the report is complete.
    """
    if not (output or "").strip():
        if not run_success:
            return f"{tool} exited with an error and printed no report.", None
        return f"{tool} printed no report.", None
    if not found_issues and not run_success:
        return (
            f"{tool} exited with an error and reported no findings: "
            f"{tool_message_excerpt(output)}",
            None,
        )
    if parse_failures:
        return malformed_report_reason(tool, parse_failures), parse_failures
    return None
