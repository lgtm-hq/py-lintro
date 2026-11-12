"""Post-processing applied to raw tool results before aggregation.

These helpers are pure transformations over :class:`ToolResult` values: doc-URL
enrichment, remaining-count fallbacks, and the dry-run filter that reduces a
check-mode result to the subset a real ``fmt`` run would actually fix. Keeping
them out of the executor lets the execute phase read as orchestration only
(issue #1823).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.models.core.tool_outcome import (
    Checked,
    FixUnmeasured,
    NoFiles,
    Skipped,
    TimedOut,
    Unavailable,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin


#: A line npm itself prints when it fails before or instead of running a tool
#: (``npm error ...`` in npm 9+, ``npm ERR! ...`` before that).
_NPM_ERROR_LINE: re.Pattern[str] = re.compile(r"^npm (?:error|ERR!) ", re.MULTILINE)

#: A shell reporting that the runner's executable was missing (exit 127):
#: ``sh: prettier: command not found`` or dash's ``sh: 1: prettier: not found``.
_NOT_FOUND_LINE: re.Pattern[str] = re.compile(
    r"(?:command not found|: not found)\s*$",
    re.MULTILINE,
)

#: ANSI escape sequences, stripped before matching.
_ANSI: re.Pattern[str] = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

#: Package runners whose own failures are not tool reports.
_RUNNERS: frozenset[str] = frozenset({"npx", "bunx"})


def _executable_name(arg: str) -> str:
    """Return the file name of ``argv[0]`` for POSIX and Windows paths alike.

    Args:
        arg: ``argv[0]`` as resolved.

    Returns:
        The final path component.
    """
    return re.split(r"[\\/]", arg)[-1]


def is_runner_command(command: Sequence[str] | None) -> bool:
    """Report whether *command* starts with an ``npx`` or ``bunx`` runner.

    Args:
        command: Executed command prefix, if known.

    Returns:
        True for an ``npx``/``bunx`` command (with or without a Windows
        ``.cmd``/``.exe`` suffix).
    """
    if not command:
        return False
    name = _executable_name(command[0]).lower()
    for suffix in (".cmd", ".exe"):
        name = name.removesuffix(suffix)
    return name in _RUNNERS


def executed_command(tool: BaseToolPlugin, result: ToolResult) -> list[str] | None:
    """Recover the command prefix a failed result ran, from its directory.

    Resolution is deterministic for a directory, so asking the plugin's own
    resolver for the result's ``cwd`` names what ran. Only failed results are
    resolved, and a result without a ``cwd`` is left unknown rather than
    guessed from lintro's own directory.

    Args:
        tool: Plugin that produced *result*.
        result: Its result.

    Returns:
        The command prefix, or None when the result passed or its directory
        is unknown.
    """
    if result.success or result.cwd is None:
        return None
    try:
        return list(tool._get_executable_command(tool.definition.name, cwd=result.cwd))
    except (OSError, ValueError, RuntimeError, AttributeError):
        return None


def _runner_failure_reason(command: Sequence[str], output: str) -> str | None:
    """Name a package-runner failure that left no tool report.

    Args:
        command: The runner command that ran.
        output: Its output, with ANSI codes removed.

    Returns:
        A one-line reason, or None when the output may be the tool's own.
    """
    runner = _executable_name(command[0])
    if not output.strip():
        return f"{runner} exited with a failure and no output"
    if _NPM_ERROR_LINE.search(output):
        return "npm failed before the tool produced a report"
    if _NOT_FOUND_LINE.search(output):
        return f"{runner} could not find the tool's executable"
    return None


def classify_runner_failure(
    result: ToolResult,
    *,
    command: Sequence[str] | None,
) -> ToolResult:
    """Report a package-runner failure as an execution failure, not a verdict.

    An ``npx`` or ``bunx`` fallback can fail before the tool runs: npm can
    reject its configuration (an npmrc ``workspace=<name>`` against
    ``--workspaces=false``), exit with no output, or fail to find the
    executable (#2043). The tool then reports nothing, so the result must never
    read as findings or as a pass. Only results whose executed command was a
    runner are considered, so a local binary that happens to print
    ``npm error`` keeps its own verdict. Such a failure, with no issues, is
    marked ``execution_failed`` with incomplete findings, and the runner's
    message stays in ``output`` as the reason.

    Args:
        result: Validated result straight from the plugin.
        command: Command prefix the result ran, from :func:`executed_command`.

    Returns:
        The result, marked as an execution failure when the runner failed.
    """
    execution = result.outcome.execution
    if (
        not is_runner_command(command)
        or result.success
        or result.execution_failed
        or result.issues_count
        or isinstance(execution, (Skipped, Unavailable, TimedOut, NoFiles))
    ):
        return result
    assert command is not None
    output = _ANSI.sub("", result.output or "")
    reason = _runner_failure_reason(command, output)
    if reason is None:
        return result
    detail = f":\n{output}" if output.strip() else "."
    return replace(
        result,
        execution_failed=True,
        findings_complete=False,
        output=f"{reason}{detail}",
    )


def get_remaining_count(result: ToolResult) -> int:
    """Get the remaining issue count from a :class:`ToolResult`.

    Falls back to ``issues_count`` when ``remaining_issues_count`` is not set.

    Args:
        result: The tool result to inspect.

    Returns:
        int: Number of remaining issues.
    """
    if result.remaining_issues_count is not None:
        return result.remaining_issues_count
    return result.issues_count


def enrich_issues_with_doc_urls(
    tool: BaseToolPlugin,
    result: ToolResult,
) -> None:
    """Populate ``doc_url`` on each issue using the plugin's doc_url method.

    Enriches both remaining issues (``result.issues``) and any pre-fix issues
    (``result.initial_issues``) so the fix-mode "Detected" and "Remaining"
    tables both show doc URLs. Skips issues that already have a doc_url set.

    Args:
        tool: Plugin instance that may provide a doc_url method.
        result: ToolResult whose issues will be enriched in-place.
    """
    if not hasattr(tool, "doc_url"):
        return

    def _enrich(issues: Sequence[BaseIssue]) -> None:
        if not issues:
            return
        for issue in issues:
            if issue.doc_url:
                continue
            # Resolve the code attribute via DISPLAY_FIELD_MAP so tools
            # that store their identifier under a different name (e.g.
            # advisory_id, vuln_id, rule_id) are handled correctly.
            field_map = issue.DISPLAY_FIELD_MAP
            code_attr = field_map.get("code", "code")
            code = str(getattr(issue, code_attr, "") or "")
            if code:
                url = tool.doc_url(code)
                if url:
                    issue.doc_url = url

    _enrich(result.issues)
    _enrich(result.initial_issues)


def issue_would_be_fixed(issue: BaseIssue) -> bool:
    """Return whether a check-mode issue belongs in the format preview.

    Resolves the issue's fixability via ``DISPLAY_FIELD_MAP`` (some tools store
    the flag under a different attribute). Tools that carry a per-issue
    fixability signal are honored — only truthy-fixable issues count. For
    example, ruff sets ``fixable`` from ruff's own ``fix`` field, so its
    non-``--fix``-able lint diagnostics are excluded.

    Issue types without a native per-issue fixability signal opt into the
    historical preview fallback via ``PREVIEW_FIXABLE``. This is an estimate
    used only for dry-run previews; public fixability stays conservative in
    totals, issue sections and structured findings. In particular, taplo,
    sqlfluff and clippy previews may include diagnostics that require an edit.

    Args:
        issue: The parsed issue to classify.

    Returns:
        bool: True if the issue should be included in the format preview.
    """
    field_map = issue.DISPLAY_FIELD_MAP
    fixable_attr = field_map.get("fixable", "fixable")
    return issue.PREVIEW_FIXABLE or bool(getattr(issue, fixable_attr, False))


def filter_result_to_fixable(result: ToolResult) -> ToolResult:
    """Return a copy of a dry-run check result limited to would-fix issues.

    Filters ``issues`` down to the preview subset (see
    :func:`issue_would_be_fixed`, including estimates for tools without a fix
    signal) and updates ``issues_count`` to match, so
    dry-run counts, the summary line, and the exit code reflect only what a
    real ``fmt`` run would actually change rather than every check-mode
    diagnostic.

    A check-mode tool sets ``success=False`` when it merely *finds* issues, but
    in dry-run those diagnostics are informational: the exit code must derive
    purely from the fixable issue count, not from the tool having found
    (possibly non-fixable) issues. Only a completed check with usable accounting
    and no parse failures can become ``success=True`` here. A timeout can carry
    partial findings; filtering those findings must preserve its failed verdict,
    as well as failures with unmeasured accounting or incomplete parsing.
    Skipped/no-files results and results without parsed issues stay unchanged.
    Dry-run only runs fix-capable tools, so a reported change without structured
    issues is treated as fixable.

    Args:
        result: The check-mode result to filter.

    Returns:
        ToolResult: A filtered copy, or the original when there are no parsed
        issues.
    """
    import dataclasses

    if not result.issues:
        return result
    outcome = result.outcome
    if isinstance(outcome.execution, (Skipped, NoFiles)):
        return result
    fixable = [issue for issue in result.issues if issue_would_be_fixed(issue)]
    completed_check = (
        isinstance(outcome.execution, Checked)
        and not isinstance(outcome.accounting, FixUnmeasured)
        and not result.parse_failures_count
    )
    return dataclasses.replace(
        result,
        issues=fixable,
        issues_count=len(fixable),
        success=outcome.success or completed_check,
    )
