"""Shared per-file check scaffolding for tool definitions.

Tools that lint one file at a time all repeat the same body: build the command
for the file, run it, parse the output into issues, classify a non-zero exit,
translate a timeout or an OS error into a per-file failure, then aggregate the
per-file results into a single ``ToolResult``. This module holds that body once
so a definition only declares the command, the parser and the two or three
policy choices that actually differ between tools.

The single-file half is exposed as :func:`check_one_file` because the fix
runner needs exactly the same step to capture a file's pre-fix issue set.

Example:
    >>> result = run_per_file_check(  # doctest: +SKIP
    ...     ctx,
    ...     plugin=self,
    ...     command=lambda f: [*self._build_command(), f],
    ...     parse=lambda output: parse_shellcheck_output(output=output),
    ... )
"""

from __future__ import annotations

import subprocess  # nosec B404 - commands are built by callers, shell disabled
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.file_processor import FileProcessingResult

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin, PreparedExecution

__all__ = [
    "DEFAULT_CHECK_POLICY",
    "PerFileCheckPolicy",
    "check_one_file",
    "run_per_file_check",
]


@dataclass(frozen=True)
class PerFileCheckPolicy:
    """The per-tool choices the shared check loop cannot infer.

    Attributes:
        failure_message: Error recorded when the command exits non-zero
            without producing any parseable issue, which means the invocation
            itself failed rather than the file being dirty. ``None`` (the
            default) uses a standard execution error and preserves raw output.
        issues_imply_failure: Mark a file unsuccessful whenever the parser
            found issues, even if the command exited zero. Needed by tools
            that report findings on a clean exit status.
        label: Progress bar label for the check pass.
        findings_exit_codes: Native exit statuses that mean lint findings.
    """

    failure_message: str | None = None
    issues_imply_failure: bool = False
    label: str = "Processing files"
    findings_exit_codes: frozenset[int] = frozenset({1})


#: Policy for a tool whose exit status is the whole verdict.
DEFAULT_CHECK_POLICY: PerFileCheckPolicy = PerFileCheckPolicy()


def check_one_file(
    *,
    plugin: BaseToolPlugin,
    cmd: list[str],
    parse: Callable[[str], Sequence[BaseIssue]],
    timeout: float,
    failure_message: str | None = None,
    issues_imply_failure: bool = False,
    findings_exit_codes: frozenset[int] = frozenset({1}),
) -> FileProcessingResult:
    """Run one check-style invocation and classify its outcome.

    Args:
        plugin: Plugin whose subprocess helper runs the command.
        cmd: Fully built command line.
        parse: Parser turning the command's output into issues.
        timeout: Per-command timeout in seconds.
        failure_message: Error to record when the command exits non-zero
            without producing any parseable issue. ``None`` uses a standard error.
        issues_imply_failure: Mark the file unsuccessful whenever issues were
            parsed, even on a zero exit status.
        findings_exit_codes: Native statuses reserved for lint findings.

    Returns:
        FileProcessingResult describing the check outcome.
    """
    output = ""
    try:
        process = plugin._run_subprocess_result(cmd=cmd, timeout=timeout)
        success, output = process.success, process.output
        # Parsing stays inside the handler: a malformed report must fail this
        # one file, not abort the whole run. ``_process_files_with_progress``
        # does not catch anything its processor raises.
        issues = list(parse(output))
    except subprocess.TimeoutExpired:
        return FileProcessingResult(
            success=False,
            output=output,
            issues=[],
            skipped=True,
            timed_out=True,
            inspected_files=False,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return FileProcessingResult(
            success=False,
            output=output,
            issues=[],
            error=str(exc),
            inspected_files=False if isinstance(exc, OSError) else None,
        )

    complete = success or (process.returncode in findings_exit_codes and bool(issues))
    if not complete:
        return FileProcessingResult(
            success=False,
            output=output,
            issues=issues,
            error=failure_message or f"Check failed (exit code {process.returncode})",
        )
    return FileProcessingResult(
        success=success and not (issues_imply_failure and issues),
        output=output,
        issues=issues,
        # Successful execution over the selected file also proves a clean
        # inspection. Failed invocations without findings remain unknown.
        inspected_files=True,
        findings_complete=True,
    )


def run_per_file_check(
    ctx: PreparedExecution,
    *,
    plugin: BaseToolPlugin,
    command: Callable[[str], list[str]],
    parse: Callable[[str], Sequence[BaseIssue]],
    policy: PerFileCheckPolicy = DEFAULT_CHECK_POLICY,
) -> ToolResult:
    """Check every prepared file one at a time and aggregate the outcome.

    Args:
        ctx: Prepared execution context from ``BaseToolPlugin.prepare``.
        plugin: Plugin the command belongs to; supplies subprocess execution,
            progress reporting and the tool name.
        command: Builds the check command for one file.
        parse: Parser turning command output into issues.
        policy: Per-tool classification and messaging choices.

    Returns:
        ToolResult carrying every issue found across the prepared files.
    """
    inspected_paths: set[str] = set()

    def process(file_path: str) -> FileProcessingResult:
        """Check one file.

        Args:
            file_path: Path of the file to check.

        Returns:
            FileProcessingResult for the aggregator.
        """
        checked = check_one_file(
            plugin=plugin,
            cmd=command(file_path),
            parse=parse,
            timeout=ctx.timeout,
            failure_message=policy.failure_message,
            issues_imply_failure=policy.issues_imply_failure,
            findings_exit_codes=policy.findings_exit_codes,
        )
        if (
            checked.inspected_files is True
            and not checked.error
            and not checked.skipped
            and not checked.timed_out
        ):
            # Unknown canonical coverage cannot authorize pruning.
            with suppress(OSError, RuntimeError):
                inspected_paths.add(str(Path(file_path).resolve()))
        return checked

    result = plugin._process_files_with_progress(
        files=ctx.files,
        processor=process,
        timeout=ctx.timeout,
        label=policy.label,
    )

    return ToolResult(
        name=plugin.definition.name,
        inspected_files=result.inspected_files,
        inspected_paths=frozenset(inspected_paths),
        execution_failed=bool(result.execution_failures),
        findings_complete=result.findings_complete and not result.execution_failures,
        success=result.all_success and result.total_issues == 0,
        output=result.build_output(timeout=ctx.timeout),
        issues_count=result.total_issues,
        timed_out=result.timed_out,
        issues=result.all_issues,
    )
