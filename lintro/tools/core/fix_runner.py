"""Shared per-file fix scaffolding for tool definitions.

Tools that fix one file at a time all repeat the same loop: lint the file to
capture the pre-fix issue set, run the fix command, optionally re-lint to see
what survived, then aggregate per-file metrics into a single ``ToolResult``.
This module holds that loop once so a definition only declares the two
commands, the parser and the handful of policy choices that actually differ
between tools.

Example:
    >>> result = run_per_file_fix(  # doctest: +SKIP
    ...     ctx,
    ...     plugin=self,
    ...     check_command=lambda f: [*base, "-d", f],
    ...     fix_command=lambda f: [*base, "-w", f],
    ...     parse=parse_shfmt_output,
    ...     policy=PerFileFixPolicy(
    ...         check_failure_message="shfmt check failed before fix",
    ...     ),
    ... )
"""

from __future__ import annotations

import subprocess  # nosec B404 - commands are built by callers, shell disabled
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.file_processor import FileFixResult, FileProcessingResult
from lintro.tools.core.check_runner import check_one_file

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin, ExecutionContext

__all__ = [
    "PerFileFixPolicy",
    "VerifyMode",
    "run_per_file_fix",
]


class VerifyMode(StrEnum):
    """How a tool determines which issues survived its fix command.

    Attributes:
        NEVER: Trust the fix command's exit status. A successful fix means
            every issue detected before it ran was resolved.
        AFTER_SUCCESS: Re-run the check command only when the fix command
            succeeded; a failed fix reports every initial issue as remaining.
        ALWAYS: Re-run the check command even when the fix command failed,
            because the tool can apply fixes partially while exiting non-zero.
    """

    NEVER = auto()
    AFTER_SUCCESS = auto()
    ALWAYS = auto()


@dataclass(frozen=True)
class PerFileFixPolicy:
    """The per-tool choices the shared fix loop cannot infer.

    Attributes:
        check_failure_message: Error recorded when the check command exits
            non-zero without producing any parseable issue, which means the
            invocation itself failed rather than the file being dirty.
        verify: When to re-run the check command after fixing.
        verify_failure_message: Error recorded when the verification run
            fails. Ignored when ``verify`` is ``VerifyMode.NEVER``.
        remaining_transform: Optional mapping applied to every issue that
            survived the fix, e.g. to clear a ``fixable`` flag once a fix has
            already been attempted and failed.
        report_verify_output: Emit the verification command's output for the
            file instead of the fix command's output. Tools whose check
            command prints the surviving diagnostics want this; tools whose
            fix command explains its own failure do not.
        summarize: Prefix the aggregated per-file output with a human-readable
            "Fixed N issue(s) in M file(s)" summary.
        label: Progress bar label for the fix pass.
    """

    check_failure_message: str
    verify: VerifyMode = VerifyMode.NEVER
    verify_failure_message: str = ""
    remaining_transform: Callable[[BaseIssue], BaseIssue] | None = None
    report_verify_output: bool = False
    summarize: bool = True
    label: str = "Fixing files"

    def __post_init__(self) -> None:
        """Reject a policy that verifies without a message for a failed verify.

        Raises:
            ValueError: If verification is enabled with an empty
                ``verify_failure_message``, which would let a broken
                verification run read as a clean fix.
        """
        if self.verify is not VerifyMode.NEVER and not self.verify_failure_message:
            msg = "verify_failure_message is required when verify is not NEVER"
            raise ValueError(msg)


@dataclass
class _FixTally:
    """Running totals collected while the per-file loop walks the files.

    Attributes:
        initial_total: Issues detected before any fix ran.
        fixed_total: Issues the fix commands resolved.
        fixed_files: Files where at least one issue was resolved.
        initial_issues: Every pre-fix issue, preserved for the two-table view.
        remaining_issues: Every issue that survived the fix attempt.
    """

    initial_total: int = 0
    fixed_total: int = 0
    fixed_files: list[str] = field(default_factory=list)
    initial_issues: list[BaseIssue] = field(default_factory=list)
    remaining_issues: list[BaseIssue] = field(default_factory=list)

    def record(self, file_path: str, result: FileFixResult) -> None:
        """Fold one file's fix result into the totals.

        Args:
            file_path: Path of the file that was processed.
            result: Per-file fix outcome to accumulate.
        """
        self.initial_total += result.initial_count
        self.fixed_total += result.fixed_count
        self.initial_issues.extend(result.initial_issues)
        self.remaining_issues.extend(result.remaining_issues)
        if result.fixed_count > 0:
            self.fixed_files.append(file_path)


def _run_check_step(
    *,
    plugin: BaseToolPlugin,
    cmd: list[str],
    parse: Callable[[str], Sequence[BaseIssue]],
    failure_message: str,
    timeout: int,
) -> FileProcessingResult:
    """Run one check-style invocation and classify its outcome.

    Delegates to the check runner so the fix loop and the check loop classify
    a timeout, an OS error and a non-zero exit identically.

    Args:
        plugin: Plugin whose subprocess helper runs the command.
        cmd: Fully built command line.
        parse: Parser turning the command's output into issues.
        failure_message: Error to record when the command exits non-zero
            without producing any parseable issue.
        timeout: Per-command timeout in seconds.

    Returns:
        FileProcessingResult describing the check outcome.
    """
    return check_one_file(
        plugin=plugin,
        cmd=cmd,
        parse=parse,
        timeout=timeout,
        failure_message=failure_message,
    )


def _failed_fix(
    *,
    initial_issues: list[BaseIssue],
    output: str,
    error: str | None = None,
    skipped: bool = False,
    timed_out: bool = False,
) -> FileFixResult:
    """Build the result for a file whose fix attempt did not resolve anything.

    Args:
        initial_issues: Issues detected before the fix ran.
        output: Diagnostic output to surface for the file.
        error: Error message, if the failure was an execution error.
        skipped: Whether the file was abandoned rather than processed.
        timed_out: Whether the failure was a subprocess timeout.

    Returns:
        FileFixResult reporting every initial issue as still remaining.
    """
    return FileFixResult(
        file_result=FileProcessingResult(
            success=False,
            output=output,
            issues=initial_issues,
            skipped=skipped,
            error=error,
            timed_out=timed_out,
        ),
        initial_count=len(initial_issues),
        fixed_count=0,
        initial_issues=initial_issues,
    )


def _verify_and_score(
    *,
    plugin: BaseToolPlugin,
    check_cmd: list[str],
    parse: Callable[[str], Sequence[BaseIssue]],
    policy: PerFileFixPolicy,
    timeout: int,
    initial_issues: list[BaseIssue],
    fix_success: bool,
    fix_output: str,
) -> FileFixResult:
    """Re-run the check command and score what the fix actually resolved.

    Args:
        plugin: Plugin whose subprocess helper runs the command.
        check_cmd: The check command to re-run.
        parse: Parser turning the command's output into issues.
        policy: Per-tool policy for messages and output selection.
        timeout: Per-command timeout in seconds.
        initial_issues: Issues detected before the fix ran.
        fix_success: Whether the fix command exited zero.
        fix_output: Output captured from the fix command.

    Returns:
        FileFixResult scored against the verification run.
    """
    transform = policy.remaining_transform
    verify = _run_check_step(
        plugin=plugin,
        cmd=check_cmd,
        parse=parse,
        failure_message=policy.verify_failure_message,
        timeout=timeout,
    )
    if verify.skipped or verify.error:
        surviving = (
            [transform(issue) for issue in initial_issues]
            if transform is not None
            else initial_issues
        )
        return FileFixResult(
            file_result=FileProcessingResult(
                success=False,
                output=verify.output or verify.error or fix_output,
                issues=surviving,
                error=verify.error or policy.verify_failure_message,
                timed_out=verify.timed_out,
            ),
            initial_count=len(initial_issues),
            fixed_count=0,
            initial_issues=initial_issues,
        )

    remaining = list(verify.issues)
    if transform is not None:
        remaining = [transform(issue) for issue in remaining]
    success = fix_success and not remaining
    if policy.report_verify_output:
        output = verify.output
    else:
        output = "" if success else fix_output
    return FileFixResult(
        file_result=FileProcessingResult(
            success=success,
            output=output,
            issues=remaining,
        ),
        initial_count=len(initial_issues),
        fixed_count=max(len(initial_issues) - len(remaining), 0),
        initial_issues=initial_issues,
    )


def _fix_one_file(
    file_path: str,
    *,
    plugin: BaseToolPlugin,
    check_command: Callable[[str], list[str]],
    fix_command: Callable[[str], list[str]],
    parse: Callable[[str], Sequence[BaseIssue]],
    policy: PerFileFixPolicy,
    timeout: int,
) -> FileFixResult:
    """Check, fix and (optionally) verify a single file.

    Args:
        file_path: Path of the file to fix.
        plugin: Plugin whose subprocess helper runs the commands.
        check_command: Builds the check command for one file.
        fix_command: Builds the fix command for one file.
        parse: Parser turning command output into issues.
        policy: Per-tool policy for verification and messages.
        timeout: Per-command timeout in seconds.

    Returns:
        FileFixResult with the per-file outcome and fix metrics.
    """
    check_cmd = check_command(file_path)
    initial = _run_check_step(
        plugin=plugin,
        cmd=check_cmd,
        parse=parse,
        failure_message=policy.check_failure_message,
        timeout=timeout,
    )
    if initial.skipped or initial.error:
        return FileFixResult(
            file_result=initial,
            initial_count=0,
            fixed_count=0,
            initial_issues=[],
        )

    initial_issues = list(initial.issues)
    if not initial_issues:
        return FileFixResult(
            file_result=FileProcessingResult(success=True, output="", issues=[]),
            initial_count=0,
            fixed_count=0,
            initial_issues=[],
        )

    try:
        fix_success, fix_output = plugin._run_subprocess(
            cmd=fix_command(file_path),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _failed_fix(
            initial_issues=initial_issues,
            output="",
            skipped=True,
            timed_out=True,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _failed_fix(
            initial_issues=initial_issues,
            output="",
            error=str(exc),
        )

    if not fix_success and policy.verify is not VerifyMode.ALWAYS:
        return _failed_fix(initial_issues=initial_issues, output=fix_output)

    if policy.verify is VerifyMode.NEVER:
        return FileFixResult(
            file_result=FileProcessingResult(success=True, output="", issues=[]),
            initial_count=len(initial_issues),
            fixed_count=len(initial_issues),
            initial_issues=initial_issues,
        )

    return _verify_and_score(
        plugin=plugin,
        check_cmd=check_cmd,
        parse=parse,
        policy=policy,
        timeout=timeout,
        initial_issues=initial_issues,
        fix_success=fix_success,
        fix_output=fix_output,
    )


def _build_summary(
    *,
    tally: _FixTally,
    remaining_count: int,
    execution_failures: int,
) -> str:
    """Render the human-readable counts line(s) for a fix run.

    Args:
        tally: Accumulated per-file fix metrics.
        remaining_count: Issues still present after the run.
        execution_failures: Files that could not be processed.

    Returns:
        Summary text, or a "No fixes needed." placeholder.
    """
    parts: list[str] = []
    if tally.fixed_total > 0:
        parts.append(
            f"Fixed {tally.fixed_total} issue(s) in {len(tally.fixed_files)} file(s)",
        )
    if remaining_count > 0:
        parts.append(f"Found {remaining_count} issue(s) that could not be fixed")
    if execution_failures > 0:
        parts.append(f"Failed to process {execution_failures} file(s)")
    return "\n".join(parts) if parts else "No fixes needed."


def run_per_file_fix(
    ctx: ExecutionContext,
    *,
    plugin: BaseToolPlugin,
    check_command: Callable[[str], list[str]],
    fix_command: Callable[[str], list[str]],
    parse: Callable[[str], Sequence[BaseIssue]],
    policy: PerFileFixPolicy,
) -> ToolResult:
    """Fix every prepared file one at a time and aggregate the outcome.

    Args:
        ctx: Prepared execution context from ``BaseToolPlugin.prepare``.
        plugin: Plugin the commands belong to; supplies subprocess execution,
            progress reporting and the tool name.
        check_command: Builds the check command for one file.
        fix_command: Builds the fix command for one file.
        parse: Parser turning command output into issues.
        policy: Per-tool verification, messaging and output choices.

    Returns:
        ToolResult carrying remaining issues plus the initial/fixed counts.
    """
    tally = _FixTally()

    def process(file_path: str) -> FileProcessingResult:
        """Fix one file and fold its metrics into the running totals.

        Args:
            file_path: Path of the file to fix.

        Returns:
            FileProcessingResult for the aggregator.
        """
        fix_result = _fix_one_file(
            file_path,
            plugin=plugin,
            check_command=check_command,
            fix_command=fix_command,
            parse=parse,
            policy=policy,
            timeout=ctx.timeout,
        )
        tally.record(file_path, fix_result)
        return fix_result.file_result

    result = plugin._process_files_with_progress(
        files=ctx.files,
        processor=process,
        timeout=ctx.timeout,
        label=policy.label,
    )

    # Count what the post-fix state actually reported rather than the
    # arithmetic remainder: a fix can change the issue set (new findings
    # surfacing after rewrites), and ``issues_count`` must match ``issues``.
    # When more issues are accounted for than the initial pass saw, grow the
    # initial total so ``initial == fixed + remaining`` stays valid.
    remaining_count = len(tally.remaining_issues)
    initial_total = max(tally.initial_total, tally.fixed_total + remaining_count)

    per_file_output = result.build_output(timeout=ctx.timeout) or ""
    if policy.summarize:
        summary = _build_summary(
            tally=tally,
            remaining_count=remaining_count,
            execution_failures=result.execution_failures,
        )
        final_output: str | None = (
            f"{summary}\n\n{per_file_output}".rstrip()
            if per_file_output.strip()
            else summary
        )
    else:
        final_output = per_file_output or None

    logger.debug(
        f"[{type(plugin).__name__}] Fix complete: initial={initial_total}, "
        f"fixed={tally.fixed_total}, remaining={remaining_count}",
    )

    return ToolResult(
        name=plugin.definition.name,
        success=result.all_success and remaining_count == 0,
        output=final_output,
        issues_count=remaining_count,
        issues=tally.remaining_issues,
        initial_issues_count=initial_total,
        fixed_issues_count=tally.fixed_total,
        remaining_issues_count=remaining_count,
        initial_issues=tally.initial_issues or None,
        timed_out=result.timed_out,
    )
