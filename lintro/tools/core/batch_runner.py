"""Shared batch check/fix scaffolding for tool definitions.

Tools that hand their whole file list to one invocation — as opposed to the
per-file loops in :mod:`lintro.tools.core.check_runner` and
:mod:`lintro.tools.core.fix_runner` — repeat the same three shapes: translate a
subprocess timeout into a ``ToolResult``, turn one ``(exit status, output)``
pair plus a parser into a ``ToolResult``, and for fix-capable tools run
check → fix → re-check and score the difference. This module holds those
shapes once so a definition only declares the command, the parser and the
policy choices that actually differ between tools.

The pieces are deliberately usable on their own: a tool whose middle section is
bespoke (a missing-config skip, a dependency-error hint, a per-module loop) can
still take :func:`batch_timeout_result` and :func:`batch_check_result` without
adopting :func:`run_batch_check`.

Example:
    >>> result = run_batch_check(  # doctest: +SKIP
    ...     ctx,
    ...     plugin=self,
    ...     cmd=[*self._build_command(), *ctx.rel_files],
    ...     parse=lambda output: parse_vale_output(output=output),
    ...     cwd=ctx.cwd,
    ... )
"""

from __future__ import annotations

import subprocess  # nosec B404 - commands are built by callers, shell disabled
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    run_subprocess_with_timeout,
    subprocess_timeout_context,
)

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin, ExecutionContext

#: The concrete issue type a definition's parser yields, threaded through the
#: runner so a per-tool timeout builder keeps its narrow signature.
IssueT = TypeVar("IssueT", bound="BaseIssue")

__all__ = [
    "DEFAULT_BATCH_CHECK_POLICY",
    "BatchCheckPolicy",
    "BatchCommands",
    "BatchFixPolicy",
    "BatchOutput",
    "BatchSuccess",
    "batch_check_result",
    "batch_fix_timeout_result",
    "batch_timeout_result",
    "run_batch_check",
    "run_batch_fix",
]


class BatchSuccess(StrEnum):
    """What makes a batch check run count as a pass.

    Attributes:
        ISSUES_ONLY: Findings fail the run even on exit zero. A declared
            findings exit code is expected only when issues were parsed;
            every other non-zero exit is an execution failure.
        EXIT_STATUS: The exit status is the whole verdict; findings are
            reported but do not by themselves fail the run.
        EXIT_AND_ISSUES: Both must be clean.
    """

    ISSUES_ONLY = auto()
    EXIT_STATUS = auto()
    EXIT_AND_ISSUES = auto()


class BatchOutput(StrEnum):
    """When a batch check run surfaces the command's raw output.

    Attributes:
        NEVER: Never surface it; the parsed issues are the only report.
        ON_FAILURE: Surface it whenever the run did not pass.
        ON_EXIT_FAILURE_WITHOUT_ISSUES: Surface it only when the command
            failed and nothing was parsed, which is the case where the raw
            text is the only available diagnosis.
        ON_ISSUES_OR_EXIT_FAILURE: Surface it when issues were found or the
            command failed.
    """

    NEVER = auto()
    ON_FAILURE = auto()
    ON_EXIT_FAILURE_WITHOUT_ISSUES = auto()
    ON_ISSUES_OR_EXIT_FAILURE = auto()


@dataclass(frozen=True)
class BatchCheckPolicy:
    """The per-tool choices the shared batch check body cannot infer.

    Attributes:
        success: How the pass/fail verdict is derived.
        output: When the command's raw output is surfaced.
        tool_name: Name used in timeout messages when it differs from the
            registered tool name.
        report_cwd: Record the working directory on the ``ToolResult``. Tools
            that emit issue paths relative to it need this so the AI layer can
            resolve them; tools that emit absolute paths do not.
        findings_exit_codes: Non-zero statuses used to report findings under
            ``ISSUES_ONLY``. Unexpected statuses and failed runs without parsed
            issues always preserve raw output, regardless of ``output``.
            The empty default treats every non-zero exit as an execution failure.
            Other success policies ignore this field.
        parse_stdout: Parse stdout alone, keeping stderr for display. Used
            only with ``ISSUES_ONLY``.
        no_files: Recognize a tool's exact ignored-all-targets response from
            its returncode, stdout and stderr. Used only with ``ISSUES_ONLY``;
            arbitrary failures must return false.
    """

    success: BatchSuccess = BatchSuccess.EXIT_AND_ISSUES
    output: BatchOutput = BatchOutput.ON_FAILURE
    tool_name: str | None = None
    report_cwd: bool = False
    findings_exit_codes: tuple[int, ...] = ()
    parse_stdout: bool = False
    no_files: Callable[[int, str, str], bool] | None = None


#: Policy for a tool whose exit status and findings must both be clean.
DEFAULT_BATCH_CHECK_POLICY: BatchCheckPolicy = BatchCheckPolicy()


@dataclass(frozen=True)
class BatchFixPolicy:
    """The per-tool wording and reporting choices for a batch fix run.

    Attributes:
        fixed_label: Noun used in the "Fixed N <label>(s)" line.
        all_fixed_message: Line emitted when every detected issue was fixed.
        verbose_output_label: Heading for the raw fix output, emitted only
            when ``verbose`` is set.
        verbose: Whether the caller asked for the raw fix output.
        report_initial_issues: Include the pre-fix issues ahead of the
            surviving ones in ``ToolResult.issues``. Tools that render a
            two-table fix view need this; the rest report only what remains.
        always_report_initial_issues: Pass an empty list rather than ``None``
            to ``ToolResult.initial_issues`` when nothing was detected.
        tool_name: Name used in timeout messages when it differs from the
            registered tool name.
        report_cwd: Record the working directory on the ``ToolResult``.
        findings_exit_codes: Non-zero statuses accepted only with parsed issues
            in any stage. The empty default fails on every non-zero exit.
        parse_stdout: Parse stdout alone, keeping stderr for display.
        parse_fix_output: Whether the write command uses the check report
            format. False for formatters that print progress during writes.
        no_files: Recognize the exact ignored-all-targets response from its
            returncode, stdout and stderr. Only the initial check may return
            a successful no-files result.
    """

    fixed_label: str = "issue"
    all_fixed_message: str = "All issues were successfully auto-fixed"
    verbose_output_label: str = "Fix output"
    verbose: bool = False
    report_initial_issues: bool = False
    always_report_initial_issues: bool = False
    tool_name: str | None = None
    report_cwd: bool = False
    findings_exit_codes: tuple[int, ...] = ()
    parse_stdout: bool = False
    parse_fix_output: bool = True
    no_files: Callable[[int, str, str], bool] | None = None


@dataclass(frozen=True)
class BatchCommands:
    """The two fully built command lines a batch fix run alternates between.

    Attributes:
        check: Command that reports what is wrong without changing anything.
            It runs twice: once before the fix and once to score it.
        fix: Command that rewrites the files.
    """

    check: list[str]
    fix: list[str]


def batch_timeout_result(
    *,
    plugin: BaseToolPlugin,
    timeout: int,
    cmd: list[str] | None = None,
    tool_name: str | None = None,
    cwd: str | None = None,
    issues: Sequence[IssueT] | None = None,
) -> ToolResult:
    """Build the ``ToolResult`` for a batch invocation that timed out.

    Follows the accounting model in :mod:`lintro.tools.core.timeout_utils`: a
    timeout is an execution failure carrying ``timed_out=True`` and never a
    synthetic issue.

    Args:
        plugin: Plugin whose name the result is reported under.
        timeout: Deadline that was exceeded, in seconds.
        cmd: Command that timed out, used only for the message.
        tool_name: Name used in the message when it differs from the
            registered tool name.
        cwd: Working directory to record on the result.
        issues: Issues to report, for callers that distinguish an empty list
            from an absent one.

    Returns:
        ToolResult describing the timeout.
    """
    timeout_result = create_timeout_result(
        tool=plugin,
        timeout=timeout,
        cmd=cmd,
        tool_name=tool_name,
    )
    return ToolResult(
        name=plugin.definition.name,
        success=timeout_result.success,
        timed_out=timeout_result.timed_out,
        output=timeout_result.output,
        issues_count=timeout_result.issues_count,
        issues=issues,
        cwd=cwd,
    )


def batch_fix_timeout_result(
    *,
    plugin: BaseToolPlugin,
    timeout: int,
    initial_issues: Sequence[IssueT],
    cmd: list[str] | None = None,
    tool_name: str | None = None,
    cwd: str | None = None,
) -> ToolResult:
    """Build the ``ToolResult`` for a fix run that timed out after its check.

    Every issue the pre-fix check detected is reported as still remaining,
    which keeps the ``initial == fixed + remaining`` invariant intact.

    Args:
        plugin: Plugin whose name the result is reported under.
        timeout: Deadline that was exceeded, in seconds.
        initial_issues: Issues detected before the fix command ran.
        cmd: Command that timed out, used only for the message.
        tool_name: Name used in the message when it differs from the
            registered tool name.
        cwd: Working directory to record on the result.

    Returns:
        ToolResult describing the timeout with the pre-fix counts.
    """
    timeout_result = create_timeout_result(
        tool=plugin,
        timeout=timeout,
        cmd=cmd,
        tool_name=tool_name,
    )
    initial_count = len(initial_issues)
    return ToolResult(
        name=plugin.definition.name,
        success=timeout_result.success,
        timed_out=timeout_result.timed_out,
        output=timeout_result.output,
        issues_count=initial_count,
        issues=list(initial_issues),
        initial_issues_count=initial_count,
        fixed_issues_count=0,
        remaining_issues_count=initial_count,
        initial_issues=list(initial_issues) if initial_issues else None,
        cwd=cwd,
    )


def _execution_failed(
    *,
    exit_success: bool,
    returncode: int | None,
    issues_count: int,
    findings_exit_codes: tuple[int, ...],
) -> bool:
    """Distinguish a declared findings exit from an execution failure.

    Args:
        exit_success: Whether the command exited zero.
        returncode: Numeric exit status, if available.
        issues_count: Number of parsed findings.
        findings_exit_codes: Non-zero statuses used to report findings.

    Returns:
        Whether the command failed without a supported findings report.
    """
    return not exit_success and not (
        returncode in findings_exit_codes and issues_count > 0
    )


def batch_check_result(
    *,
    plugin: BaseToolPlugin,
    exit_success: bool,
    output: str,
    issues: Sequence[IssueT],
    policy: BatchCheckPolicy = DEFAULT_BATCH_CHECK_POLICY,
    cwd: str | None = None,
    returncode: int | None = None,
) -> ToolResult:
    """Turn one batch invocation's outcome into a ``ToolResult``.

    Args:
        plugin: Plugin whose name the result is reported under.
        exit_success: Whether the command exited zero.
        output: Raw combined output of the command.
        issues: Issues the parser extracted from ``output``.
        policy: How to derive the verdict and when to surface ``output``.
        cwd: Working directory to record on the result.
        returncode: Numeric exit status, required to recognize a declared
            findings exit code under ``ISSUES_ONLY``. When present, this is
            the source of truth for ``exit_success``.

    Returns:
        ToolResult for the run.

    Raises:
        ValueError: If the policy names a ``BatchSuccess`` or ``BatchOutput``
            member this function does not handle.
    """
    if returncode is not None:
        exit_success = returncode == 0
    issues_count = len(issues)
    execution_failed = False
    match policy.success:
        case BatchSuccess.ISSUES_ONLY:
            execution_failed = _execution_failed(
                exit_success=exit_success,
                returncode=returncode,
                issues_count=issues_count,
                findings_exit_codes=policy.findings_exit_codes,
            )
            success = not execution_failed and issues_count == 0
        case BatchSuccess.EXIT_STATUS:
            success = exit_success
        case BatchSuccess.EXIT_AND_ISSUES:
            success = exit_success and issues_count == 0
        case _:
            msg = f"unsupported BatchSuccess member: {policy.success}"
            raise ValueError(msg)

    match policy.output:
        case BatchOutput.NEVER:
            show_output = False
        case BatchOutput.ON_FAILURE:
            show_output = not success
        case BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES:
            show_output = not exit_success and issues_count == 0
        case BatchOutput.ON_ISSUES_OR_EXIT_FAILURE:
            show_output = issues_count > 0 or not exit_success
        case _:
            msg = f"unsupported BatchOutput member: {policy.output}"
            raise ValueError(msg)

    return ToolResult(
        name=plugin.definition.name,
        success=success,
        output=output if show_output or execution_failed else None,
        issues_count=issues_count,
        issues=list(issues),
        cwd=cwd,
    )


def run_batch_check(
    ctx: ExecutionContext,
    *,
    plugin: BaseToolPlugin,
    cmd: list[str],
    parse: Callable[[str], Sequence[IssueT]],
    policy: BatchCheckPolicy = DEFAULT_BATCH_CHECK_POLICY,
    cwd: str | None = None,
    on_timeout: Callable[[], ToolResult] | None = None,
    on_error: Callable[[Exception], ToolResult] | None = None,
) -> ToolResult:
    """Run one batch check invocation and classify its outcome.

    Args:
        ctx: Prepared execution context from ``BaseToolPlugin.prepare``.
        plugin: Plugin the command belongs to; supplies subprocess execution
            and the tool name.
        cmd: Fully built command line, file arguments included.
        parse: Parser turning the command's output into issues.
        policy: How to derive the verdict, when to surface the output, and
            whether the working directory is reported.
        cwd: Working directory the command runs in.
        on_timeout: Builds the result for a timeout. Defaults to
            :func:`batch_timeout_result`.
        on_error: Builds the result for a launch or execution error. When
            ``None`` (the default) such errors propagate to the caller.

    Returns:
        ToolResult for the run.

    Raises:
        OSError: Re-raised when the command cannot be launched and no
            ``on_error`` handler was supplied.
        ValueError: Re-raised when the invocation fails and no ``on_error``
            handler was supplied.
        RuntimeError: Re-raised when the invocation fails and no ``on_error``
            handler was supplied.
    """
    result_cwd = cwd if policy.report_cwd else None
    returncode = None
    parse_output = ""
    try:
        if policy.success == BatchSuccess.ISSUES_ONLY:
            with subprocess_timeout_context(
                plugin,
                cmd,
                ctx.timeout,
                policy.tool_name,
            ):
                process = plugin._run_subprocess_result(
                    cmd=cmd,
                    timeout=ctx.timeout,
                    cwd=cwd,
                )
            exit_success, output = process.success, process.output
            returncode = process.returncode
            if policy.no_files is not None and policy.no_files(
                process.returncode,
                process.stdout,
                process.stderr,
            ):
                return ToolResult(
                    name=plugin.definition.name,
                    success=True,
                    no_files=True,
                    output=output,
                    issues=[],
                    cwd=result_cwd,
                )
            parse_output = process.stdout if policy.parse_stdout else output
        else:
            exit_success, output = run_subprocess_with_timeout(
                tool=plugin,
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=cwd,
                tool_name=policy.tool_name,
            )
            parse_output = output
    except subprocess.TimeoutExpired:
        if on_timeout is not None:
            return on_timeout()
        return batch_timeout_result(
            plugin=plugin,
            timeout=ctx.timeout,
            cmd=cmd,
            tool_name=policy.tool_name,
            cwd=result_cwd,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        if on_error is None:
            raise
        return on_error(exc)

    return batch_check_result(
        plugin=plugin,
        exit_success=exit_success,
        output=output,
        issues=parse(parse_output),
        policy=policy,
        cwd=result_cwd,
        returncode=returncode,
    )


def _build_fix_summary(
    *,
    policy: BatchFixPolicy,
    fixed_count: int,
    remaining_issues: Sequence[IssueT],
    fix_output: str,
) -> str | None:
    """Render the human-readable report for a batch fix run.

    Args:
        policy: Per-tool wording choices.
        fixed_count: Issues the fix command resolved.
        remaining_issues: Issues that survived the fix.
        fix_output: Raw output of the fix command.

    Returns:
        The report, or ``None`` when there is nothing to say.
    """
    lines: list[str] = []
    if fixed_count > 0:
        lines.append(f"Fixed {fixed_count} {policy.fixed_label}(s)")

    remaining_count = len(remaining_issues)
    if remaining_count > 0:
        lines.append(f"Found {remaining_count} issue(s) that cannot be auto-fixed")
        for issue in remaining_issues[:5]:
            lines.append(f"  {issue.file} - {issue.message}")
        if remaining_count > 5:
            lines.append(f"  ... and {remaining_count - 5} more")
    elif fixed_count > 0:
        lines.append(policy.all_fixed_message)

    if policy.verbose and fix_output and fix_output.strip():
        lines.append(f"{policy.verbose_output_label}:\n{fix_output}")

    return "\n".join(lines) if lines else None


def _batch_fix_failure_result(
    *,
    plugin: BaseToolPlugin,
    output: str,
    initial_issues: Sequence[IssueT],
    policy: BatchFixPolicy,
    initial_check: bool,
    cwd: str | None,
) -> ToolResult:
    """Keep diagnostics and avoid claiming fixes after an execution failure.

    Args:
        plugin: Plugin whose name the result is reported under.
        output: Raw output from the failed stage, including an empty report.
        initial_issues: Findings seen before attempting to fix files.
        policy: Per-tool issue reporting choices.
        initial_check: Whether the failure occurred before any writes.
        cwd: Working directory to report on the result.

    Returns:
        A failed result carrying the observed pre-fix findings. An execution
        failure leaves no verified residual or fix count.
    """
    reported_issues = list(initial_issues)
    return ToolResult(
        name=plugin.definition.name,
        success=False,
        output=output,
        issues_count=len(reported_issues),
        issues=reported_issues,
        initial_issues_count=len(initial_issues),
        fixed_issues_count=None,
        remaining_issues_count=None,
        residual_unknown=True,
        residual_unknown_reason=(
            "Batch initial check failed before writes"
            if initial_check
            else "Batch execution failed after writes may have begun"
        ),
        initial_issues=(
            list(initial_issues)
            if initial_issues or policy.always_report_initial_issues
            else None
        ),
        cwd=cwd,
    )


def run_batch_fix(
    ctx: ExecutionContext,
    *,
    plugin: BaseToolPlugin,
    commands: BatchCommands,
    parse: Callable[[str], Sequence[IssueT]],
    policy: BatchFixPolicy,
    cwd: str | None = None,
    on_timeout: Callable[[Sequence[IssueT]], ToolResult] | None = None,
    on_error: Callable[[Exception], ToolResult] | None = None,
) -> ToolResult:
    """Check, fix and re-check a whole file list in one pass each.

    The pre-fix check establishes what was wrong, the fix command rewrites the
    files, and the same check command run again establishes what survived. The
    difference is the fixed count. An execution failure in any stage stops the
    pipeline and preserves that stage's output without claiming verified fixes.

    Args:
        ctx: Prepared execution context from ``BaseToolPlugin.prepare``.
        plugin: Plugin the commands belong to.
        commands: The check and fix command lines, file arguments included.
        parse: Parser turning command output into issues.
        policy: Per-tool wording and reporting choices.
        cwd: Working directory the commands run in.
        on_timeout: Builds the result for a timeout, given the issues detected
            so far (empty for a timeout in the pre-fix check). Defaults to
            :func:`batch_fix_timeout_result`.
        on_error: Builds the result for a launch or execution error. When
            ``None`` (the default) such errors propagate to the caller.

    Returns:
        ToolResult carrying the surviving issues and the fix counts.
    """
    initial_issues: list[IssueT] = []
    result_cwd = cwd if policy.report_cwd else None

    def run_stage(
        cmd: list[str],
        *,
        initial_check: bool = False,
        fixing: bool = False,
    ) -> tuple[str, list[IssueT]] | ToolResult:
        """Run one stage of the fix pipeline.

        Args:
            cmd: Command line for this stage.
            initial_check: Whether this stage establishes the pre-fix findings.
            fixing: Whether this stage writes files rather than checking them.

        Returns:
            The command's output and parsed issues, or an early failure result.

        Raises:
            OSError: Re-raised when the command cannot be launched and no
                ``on_error`` handler was supplied.
            ValueError: Re-raised when the invocation fails and no
                ``on_error`` handler was supplied.
            RuntimeError: Re-raised when the invocation fails and no
                ``on_error`` handler was supplied.
        """
        try:
            with subprocess_timeout_context(
                plugin,
                cmd,
                ctx.timeout,
                policy.tool_name,
            ):
                process = plugin._run_subprocess_result(
                    cmd=cmd,
                    timeout=ctx.timeout,
                    cwd=cwd,
                )
        except subprocess.TimeoutExpired:
            if on_timeout is not None:
                return on_timeout(initial_issues)
            return batch_fix_timeout_result(
                plugin=plugin,
                timeout=ctx.timeout,
                initial_issues=initial_issues,
                cmd=cmd,
                tool_name=policy.tool_name,
                cwd=result_cwd,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            if on_error is None:
                raise
            return on_error(exc)
        if (
            initial_check
            and policy.no_files is not None
            and policy.no_files(
                process.returncode,
                process.stdout,
                process.stderr,
            )
        ):
            return ToolResult(
                name=plugin.definition.name,
                success=True,
                no_files=True,
                output=process.output,
                issues=[],
                cwd=result_cwd,
            )
        parse_output = process.stdout if policy.parse_stdout else process.output
        stage_issues = (
            list(parse(parse_output)) if not fixing or policy.parse_fix_output else []
        )
        if initial_check:
            initial_issues.extend(stage_issues)
        if _execution_failed(
            exit_success=process.success,
            returncode=process.returncode,
            issues_count=len(stage_issues),
            findings_exit_codes=policy.findings_exit_codes,
        ):
            return _batch_fix_failure_result(
                plugin=plugin,
                output=process.output,
                initial_issues=initial_issues,
                policy=policy,
                initial_check=initial_check,
                cwd=result_cwd,
            )
        return process.output, stage_issues

    check_output = run_stage(commands.check, initial_check=True)
    if isinstance(check_output, ToolResult):
        return check_output

    fix_output = run_stage(commands.fix, fixing=True)
    if isinstance(fix_output, ToolResult):
        return fix_output

    verify_output = run_stage(commands.check)
    if isinstance(verify_output, ToolResult):
        return verify_output

    remaining_issues = verify_output[1]
    remaining_count = len(remaining_issues)
    fixed_count = max(0, len(initial_issues) - remaining_count)
    # Count what the post-fix state actually reported rather than the
    # arithmetic remainder: a fix can surface findings the first pass did not
    # see. When more issues are accounted for than the initial pass saw, grow
    # the initial total so ``initial == fixed + remaining`` stays valid — the
    # same guard the per-file fix runner applies.
    initial_count = max(len(initial_issues), fixed_count + remaining_count)

    reported_issues: list[IssueT] = (
        [*initial_issues, *remaining_issues]
        if policy.report_initial_issues
        else list(remaining_issues)
    )
    if policy.always_report_initial_issues:
        reported_initial: Sequence[IssueT] | None = initial_issues
    else:
        reported_initial = initial_issues or None

    logger.debug(
        f"[{type(plugin).__name__}] Batch fix complete: initial={initial_count}, "
        f"fixed={fixed_count}, remaining={remaining_count}",
    )

    return ToolResult(
        name=plugin.definition.name,
        success=remaining_count == 0,
        output=_build_fix_summary(
            policy=policy,
            fixed_count=fixed_count,
            remaining_issues=remaining_issues,
            fix_output=fix_output[0],
        ),
        issues_count=remaining_count,
        issues=reported_issues,
        initial_issues_count=initial_count,
        fixed_issues_count=fixed_count,
        remaining_issues_count=remaining_count,
        initial_issues=reported_initial,
        cwd=result_cwd,
    )
