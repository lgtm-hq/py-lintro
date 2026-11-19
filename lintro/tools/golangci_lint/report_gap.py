"""Tell a golangci-lint run that printed no complete report apart (#3352).

With ``--output.json.path stdout`` golangci-lint prints its JSON report on
every completed run and writes its logs and errors to stderr, which the
subprocess boundary appends after stdout. These helpers name why one
invocation produced no complete verdict, without dumping the raw report.
"""

from __future__ import annotations

import json
from pathlib import Path

from lintro.tools.core.incomplete_run import (
    malformed_report_reason,
    tool_message_excerpt,
)


def report_gap(
    *,
    run_success: bool,
    output: str | None,
    found_issues: bool,
    parse_failures: int,
) -> tuple[str, int | None] | None:
    """Name why one golangci-lint invocation produced no complete verdict.

    With ``--output.json.path stdout`` golangci-lint prints its JSON report on
    every completed run, clean or not, and writes its own logs and errors to
    stderr, which the subprocess boundary appends after stdout. A failed exit
    whose output holds no ``Issues`` report is therefore golangci-lint failing
    to run (a config, build or lock error), not a malformed report, and is
    never counted as a parse failure (#3352). A report with invalid records is
    malformed and keeps its count. A valid report behind a failed exit that
    lists no findings (the text-only or JSON concurrency exit 3) is an
    execution failure with zero parse failures.

    Args:
        run_success: Whether golangci-lint exited 0.
        output: Combined output of the invocation.
        found_issues: Whether any finding was parsed.
        parse_failures: Invalid report containers or records.

    Returns:
        The reason and the parse-failure count to report (None when no report
        was printed), or None when the report is complete.
    """
    text = (output or "").strip()
    if not text:
        if run_success:
            return "golangci-lint printed no report.", None
        return "golangci-lint exited with an error and printed no report.", None
    if not run_success and '"Issues"' not in text:
        return (
            "golangci-lint exited with an error and printed no report: "
            f"{tool_message_excerpt(text)}",
            None,
        )
    if parse_failures:
        return malformed_report_reason("golangci-lint", parse_failures), parse_failures
    if not run_success and not found_issues:
        # Quote golangci-lint's own message, not the (empty) report itself.
        detail = tool_message_excerpt(text_beside_report(text))
        return (
            f"golangci-lint exited with an error and reported no findings: {detail}",
            0,
        )
    return None


def text_beside_report(text: str) -> str:
    """Return a combined output with its leading JSON report cut out.

    Args:
        text: Output holding one decodable JSON report.

    Returns:
        The text before and after the report.
    """
    start = text.find("{")
    try:
        _, end = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return text
    return f"{text[:start]}\n{text[end:]}"


def module_reason(reason: str, *, module_root: Path, labelled: bool) -> str:
    """Name the module a reason belongs to when several modules ran.

    Args:
        reason: Why the module's run is incomplete.
        module_root: Directory golangci-lint ran in.
        labelled: Whether to prefix the module directory.

    Returns:
        The reason, prefixed with the module directory when labelled.
    """
    return f"{module_root}: {reason}" if labelled else reason
