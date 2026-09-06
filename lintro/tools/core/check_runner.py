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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.file_processor import FileProcessingResult

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin, ExecutionContext

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
            default) leaves such a run reported as an unsuccessful file with
            its raw output, which is what most wrapped tools want: their exit
            status alone is a reliable verdict.
        issues_imply_failure: Mark a file unsuccessful whenever the parser
            found issues, even if the command exited zero. Needed by tools
            that report findings on a clean exit status.
        label: Progress bar label for the check pass.
    """

    failure_message: str | None = None
    issues_imply_failure: bool = False
    label: str = "Processing files"


#: Policy for a tool whose exit status is the whole verdict.
DEFAULT_CHECK_POLICY: PerFileCheckPolicy = PerFileCheckPolicy()


def check_one_file(
    *,
    plugin: BaseToolPlugin,
    cmd: list[str],
    parse: Callable[[str], Sequence[BaseIssue]],
    timeout: int,
    failure_message: str | None = None,
    issues_imply_failure: bool = False,
) -> FileProcessingResult:
    """Run one check-style invocation and classify its outcome.

    Args:
        plugin: Plugin whose subprocess helper runs the command.
        cmd: Fully built command line.
        parse: Parser turning the command's output into issues.
        timeout: Per-command timeout in seconds.
        failure_message: Error to record when the command exits non-zero
            without producing any parseable issue. ``None`` reports the run as
            an unsuccessful file rather than an execution failure.
        issues_imply_failure: Mark the file unsuccessful whenever issues were
            parsed, even on a zero exit status.

    Returns:
        FileProcessingResult describing the check outcome.
    """
    try:
        success, output = plugin._run_subprocess(cmd=cmd, timeout=timeout)
        # Parsing stays inside the handler: a malformed report must fail this
        # one file, not abort the whole run. ``_process_files_with_progress``
        # does not catch anything its processor raises.
        issues = list(parse(output))
    except subprocess.TimeoutExpired:
        return FileProcessingResult(
            success=False,
            output="",
            issues=[],
            skipped=True,
            timed_out=True,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return FileProcessingResult(
            success=False,
            output="",
            issues=[],
            error=str(exc),
        )

    if not success and not issues and failure_message is not None:
        return FileProcessingResult(
            success=False,
            output=output,
            issues=[],
            error=failure_message,
        )
    return FileProcessingResult(
        success=success and not (issues_imply_failure and issues),
        output=output,
        issues=issues,
    )


def run_per_file_check(
    ctx: ExecutionContext,
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

    def process(file_path: str) -> FileProcessingResult:
        """Check one file.

        Args:
            file_path: Path of the file to check.

        Returns:
            FileProcessingResult for the aggregator.
        """
        return check_one_file(
            plugin=plugin,
            cmd=command(file_path),
            parse=parse,
            timeout=ctx.timeout,
            failure_message=policy.failure_message,
            issues_imply_failure=policy.issues_imply_failure,
        )

    result = plugin._process_files_with_progress(
        files=ctx.files,
        processor=process,
        timeout=ctx.timeout,
        label=policy.label,
    )

    return ToolResult(
        name=plugin.definition.name,
        success=result.all_success and result.total_issues == 0,
        output=result.build_output(timeout=ctx.timeout),
        issues_count=result.total_issues,
        timed_out=result.timed_out,
        issues=result.all_issues,
    )
