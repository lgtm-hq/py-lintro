"""Tell a golangci-lint run that printed no complete report apart (#3352).

With ``--output.json.path stdout`` golangci-lint prints its JSON report on
every completed run and writes its logs and errors to stderr, which the
subprocess boundary appends after stdout. These helpers name why one
invocation produced no complete verdict, without dumping the raw report.
"""

from __future__ import annotations

import os
from pathlib import Path

from lintro.tools.core import incomplete_run
from lintro.tools.core.incomplete_run import text_beside_report, tool_message_excerpt

#: Text every golangci-lint JSON report holds, clean or not.
_REPORT_MARKER: str = '"Issues"'

#: Log levels golangci-lint uses for messages that do not stop a run.
_NOTICE_LEVELS: tuple[str, ...] = ("level=warning", "level=info", "level=debug")

#: Lower-cased text of golangci-lint's warning for fixes it could not write.
_FIX_FAILED: str = "failed to fix"


def report_gap(
    *,
    run_success: bool,
    output: str | None,
    found_issues: bool,
    parse_failures: int,
) -> tuple[str, int | None] | None:
    """Name why one golangci-lint invocation produced no complete verdict.

    A failed exit whose output holds no ``Issues`` report is golangci-lint
    failing to run (a config, build or lock error), not a malformed report,
    and is never counted as a parse failure (#3352). A report with invalid
    records is malformed and keeps its count. A valid report behind a failed
    exit that lists no findings (the text-only or JSON concurrency exit 3) is
    an execution failure with zero parse failures.

    Args:
        run_success: Whether golangci-lint exited 0.
        output: Combined output of the invocation.
        found_issues: Whether any finding was parsed.
        parse_failures: Invalid report containers or records.

    Returns:
        The reason and the parse-failure count to report (None when no report
        was printed), or None when the report is complete.
    """
    return incomplete_run.report_gap(
        tool="golangci-lint",
        output=output,
        run_success=run_success,
        found_issues=found_issues,
        parse_failures=parse_failures,
        report_marker=_REPORT_MARKER,
    )


def fix_diagnostic_gap(
    output: str | None,
    *,
    module_root: Path,
) -> tuple[str, int] | None:
    """Name an error a ``--fix`` run logged beside a complete report.

    Valid findings do not show the fixer finished: golangci-lint exits 1 with
    a full report when it could not write a fix (a read-only file, say) and
    only logs ``[runner] Failed to fix issues: ...``. That line, an
    error-level log line, or any other text that is not a warning, info or
    debug log is an execution diagnostic, so the fix result is incomplete.

    Args:
        output: Combined output of the ``--fix`` invocation.
        module_root: Directory golangci-lint ran in; paths under it are
            quoted relative to it so the quote keeps the error itself.

    Returns:
        The reason and a zero parse-failure count, or None when golangci-lint
        logged nothing beyond notices.
    """
    text = (output or "").strip()
    if _REPORT_MARKER not in text:
        return None
    diagnostics = [
        line.strip()
        for line in text_beside_report(text).splitlines()
        if line.strip()
        and (not line.strip().startswith(_NOTICE_LEVELS) or _FIX_FAILED in line.lower())
    ]
    if not diagnostics:
        return None
    quoted = "\n".join(diagnostics)
    for root in {str(module_root), str(module_root.resolve())}:
        quoted = quoted.replace(root + os.sep, "")
    return (
        "golangci-lint --fix reported an error, so the fix is incomplete: "
        f"{tool_message_excerpt(quoted)}",
        0,
    )


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
