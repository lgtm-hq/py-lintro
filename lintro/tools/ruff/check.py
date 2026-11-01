"""Ruff check execution logic.

Functions for running ruff check commands and processing results.
"""

import json
import subprocess  # nosec B404 - subprocess used safely to execute ruff commands with controlled input
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.parsers.ruff.ruff_format_coverage import (
    format_inspected_paths,
    format_report_parse_failures,
)
from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.ruff.ruff_parser import (
    parse_ruff_format_check_output,
    parse_ruff_output_with_stats,
)
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    run_subprocess_result_with_timeout,
)

# Re-exported so callers keep a single source of truth for the default Ruff
# timeout (defined in lintro.tools.ruff.definition). Importing here preserves
# the historical ``from ...ruff.check import RUFF_DEFAULT_TIMEOUT`` entry point.
from lintro.tools.ruff.definition import RUFF_DEFAULT_TIMEOUT
from lintro.tools.ruff.paths import absolute_issue_paths

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.ruff.definition import RuffPlugin

__all__ = ["RUFF_DEFAULT_TIMEOUT", "execute_ruff_check"]


def _lint_parse_failures(output: str, issues: list[RuffIssue]) -> int:
    """Account for the entire native JSON array before certifying coverage.

    Args:
        output: Ruff's stdout, excluding stderr diagnostics.
        issues: Findings recovered by the tolerant display parser.

    Returns:
        Number of missing or malformed diagnostic records.
    """
    try:
        records = json.loads(output)
    except (ValueError, TypeError):
        return 1
    if not isinstance(records, list):
        return 1
    invalid = sum(
        not issue.file or not issue.message or issue.line <= 0 or issue.column <= 0
        for issue in issues
    )
    return abs(len(records) - len(issues)) + invalid


def _baseline_coverage(
    tool: "RuffPlugin",
    *,
    cmd: list[str],
    cwd: str | None,
    timeout: int,
) -> frozenset[str] | None:
    """Ask Ruff which explicit inputs survived its native exclusions.

    Args:
        tool: Configured Ruff plugin.
        cmd: Original check command with identical configuration and inputs.
        cwd: Original working directory.
        timeout: Subprocess deadline.

    Returns:
        Exact native discovery coverage, or None if it cannot be measured.
    """
    if not tool.options.get("_baseline_coverage", False):
        return None
    try:
        process = run_subprocess_result_with_timeout(
            tool=tool,
            cmd=[*cmd, "--show-files"],
            timeout=timeout,
            cwd=cwd,
        )
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None
    if not process.success:
        return None
    try:
        output = process.stdout
        paths = [Path(line) for line in output.splitlines() if line.strip()]
        if any(not path.is_absolute() or not path.is_file() for path in paths):
            return None
        return frozenset(str(path.resolve()) for path in paths)
    except (OSError, RuntimeError):
        return None


def execute_ruff_check(
    tool: "RuffPlugin",
    paths: list[str],
) -> "ToolResult":
    """Execute ruff check command and process results.

    Args:
        tool: RuffTool instance
        paths: list[str]: List of file or directory paths to check.

    Returns:
        ToolResult: ToolResult instance.
    """
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.ruff.commands import (
        build_ruff_check_command,
        build_ruff_format_command,
    )

    # Delegate file discovery, version checking, cwd/rel-path computation, and
    # timeout resolution to the shared preparation pipeline so ruff behaves
    # consistently with every other tool plugin.
    ctx = tool.prepare(
        paths=paths,
        options={},
        no_files_message="No files to check.",
    )
    if isinstance(ctx, ToolResult):
        return ctx

    # Ruff must run from the common parent directory of the target files (so it
    # discovers the correct configuration) and receive file paths relative to
    # that directory.
    cwd: str | None = ctx.cwd
    rel_files: list[str] = ctx.rel_files
    timeout: int = int(ctx.timeout)
    # Lint check
    cmd: list[str] = build_ruff_check_command(tool=tool, files=rel_files, fix=False)
    success_lint: bool
    output_lint: str
    try:
        lint_process = run_subprocess_result_with_timeout(
            tool=tool,
            cmd=cmd,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        timeout_result = create_timeout_result(
            tool=tool,
            timeout=timeout,
            cmd=cmd,
        )
        return ToolResult(
            name=tool.definition.name,
            success=timeout_result.success,
            timed_out=timeout_result.timed_out,
            output=timeout_result.output,
            issues_count=timeout_result.issues_count,
            issues=timeout_result.issues,
        )

    success_lint, output_lint = lint_process.success, lint_process.output
    # Debug logging for CI diagnostics
    logger.debug(f"[ruff] check command: {' '.join(cmd)}")
    logger.debug(f"[ruff] check success: {success_lint}")
    if not success_lint:
        # Log full output to debug file only - raw JSON output is parsed and
        # formatted into tables, so no need to show it in console warnings
        logger.debug(f"[ruff] check full output:\n{output_lint}")

    lint_issues, parse_failures = parse_ruff_output_with_stats(
        output=lint_process.stdout,
    )
    lint_issues_count: int = len(lint_issues)
    parse_failures = max(
        parse_failures,
        _lint_parse_failures(lint_process.stdout, lint_issues),
    )
    lint_complete = not parse_failures and (
        success_lint or (lint_process.returncode == 1 and bool(lint_issues))
    )

    # Optional format check via `format_check` flag
    format_issues_count: int = 0
    format_files: list[str] = []
    format_issues: list[RuffFormatIssue] = []
    success_format: bool = True  # Default to True when format check is skipped
    format_complete = True
    format_coverage: frozenset[str] | None = None
    output_format = ""
    if tool.options.get("format_check", False):
        format_cmd: list[str] = build_ruff_format_command(
            tool=tool,
            files=rel_files,
            check_only=True,
        )
        if tool.options.get("_baseline_coverage", False):
            format_cmd.append("--verbose")
        try:
            format_process = run_subprocess_result_with_timeout(
                tool=tool,
                cmd=format_cmd,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=tool,
                timeout=timeout,
                cmd=format_cmd,
            )
            return ToolResult(
                name=tool.definition.name,
                success=timeout_result.success,
                timed_out=timeout_result.timed_out,
                output=timeout_result.output,
                issues_count=lint_issues_count + timeout_result.issues_count,
                issues=lint_issues + timeout_result.issues,
                parse_failures_count=parse_failures,
            )

        success_format, output_format = format_process.success, format_process.output
        # Debug logging for CI diagnostics
        logger.debug(f"[ruff] format --check command: {' '.join(format_cmd)}")
        logger.debug(f"[ruff] format --check success: {success_format}")
        if not success_format:
            # Log full output to debug file only - output is parsed and
            # formatted into tables, so no need to show it in console warnings
            logger.debug(f"[ruff] format check full output:\n{output_format}")

        format_files = parse_ruff_format_check_output(output=output_format)
        format_failures = format_report_parse_failures(output_format, format_files)
        parse_failures += format_failures
        format_complete = not format_failures and (
            success_format or (format_process.returncode == 1 and bool(format_files))
        )
        if format_complete and tool.options.get("_baseline_coverage", False):
            format_coverage = format_inspected_paths(output_format)
        # Normalize files to absolute paths to keep behavior consistent with
        # direct CLI calls and stabilize tests that compare exact paths. The
        # fix flow canonicalises the same way through the same helper, so a
        # file cannot be keyed two ways across a mutate-then-verify run.
        normalized_files = absolute_issue_paths(files=format_files, cwd=cwd)
        format_issues_count = len(normalized_files)
        format_issues = [RuffFormatIssue(file=file) for file in normalized_files]

    # Combine results - respect subprocess exit codes and issue counts
    issues_count: int = lint_issues_count + format_issues_count
    complete = lint_complete and format_complete
    success: bool = success_lint and success_format and issues_count == 0 and complete

    if parse_failures:
        logger.warning(
            f"ruff: {parse_failures} diagnostic record(s) could not be parsed",
        )
    # A failed process with no findings needs an execution-error diagnostic.
    elif not success and issues_count == 0:
        logger.warning(
            f"ruff subprocess failed (lint={success_lint}, format={success_format}) "
            f"but no issues were parsed - this indicates a ruff execution error",
        )

    # Suppress narrative blocks; rely on standardized tables and summary lines
    output_summary = (
        "\n".join(filter(None, (output_lint, output_format))) if not complete else None
    )

    # Combine linting and formatting issues for the formatters
    all_issues = lint_issues + format_issues

    coverage = (
        _baseline_coverage(tool, cmd=cmd, cwd=cwd, timeout=timeout)
        if complete
        else None
    )
    if coverage is not None and format_coverage is not None:
        # A single file set can certify both capabilities only over their
        # intersection. Each native command can have different exclusions.
        coverage &= format_coverage

    return ToolResult(
        name=tool.definition.name,
        inspected_files=True if complete else None,
        findings_complete=complete,
        execution_failed=not complete,
        parse_failures_count=parse_failures,
        inspected_paths=coverage,
        uninspected_codes=(
            frozenset({"FORMAT"}) if format_coverage is None else frozenset()
        ),
        success=success,
        output=output_summary,
        issues_count=issues_count,
        issues=all_issues,
        cwd=cwd,
    )
