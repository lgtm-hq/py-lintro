"""Report a command lintro refused to spawn as an incomplete run (#3486).

These helpers sit outside :mod:`lintro.utils.execution`, whose package
imports the executors and therefore :mod:`lintro.tools`, so the verify pass
in ``lintro.tools.core`` can mark a refused check without an import cycle
(#3498). They moved here from :mod:`lintro.utils.execution.result_shaping`;
import them from this module.
"""

from __future__ import annotations

from dataclasses import replace

from lintro.exceptions.errors import UnsafeCommandError
from lintro.models.core.tool_outcome import (
    Checked,
    FixAccounting,
    FixPartial,
    FixUnmeasured,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.utils.npx_integrity import NpxInstallError


def command_refusal(tool: object) -> UnsafeCommandError | None:
    """Return the command *tool* refused to spawn during its run, if any.

    Args:
        tool: Plugin instance that ran, or None when none was built.

    Returns:
        The recorded :class:`UnsafeCommandError`, or None.
    """
    refusal = getattr(tool, "command_refusal", None)
    return refusal if isinstance(refusal, UnsafeCommandError) else None


def refused_command_failure(
    tool: object,
    *,
    name: str,
    fix: bool,
) -> ToolResult | None:
    """Build the result for a refusal that escaped the plugin as an exception.

    A caller running a plugin outside the executors uses this in its
    exception handler, so an escaped refusal is reported the same way the
    executors report it instead of as a skip or a crash (#3493).

    Args:
        tool: Plugin instance that ran, or None when none was built.
        name: Tool name for the result.
        fix: Whether the run was a fix.

    Returns:
        A failed result marked by :func:`mark_refused_command`, or None when
        the plugin refused no command and the exception is something else.
    """
    if command_refusal(tool) is None:
        return None
    failed = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=False)),
        name=name,
    )
    return mark_refused_command(tool, failed, fix=fix)


def mark_refused_command(
    tool: object,
    result: ToolResult,
    *,
    fix: bool,
) -> ToolResult:
    """Report a run that lintro refused to spawn as an execution failure.

    :func:`~lintro.plugins.subprocess_executor.validate_subprocess_command`
    refuses a command it cannot run safely, for example an argument that
    ``cmd.exe`` would interpret when the tool is a ``.cmd`` shim (#3486). The
    plugin records the refusal on ``command_refusal``. Plugins handle the
    ``ValueError`` in different ways, and some turn it into an ordinary
    failed verdict, so the executor applies this after every run: the tool
    never saw that command, so its result is marked ``execution_failed`` with
    incomplete findings and unknown inspection. Findings the tool really
    reported on other invocations are kept, and the refusal leads the output.

    Args:
        tool: Plugin that produced *result*, or None when none was built.
        result: Its result, or the failed result built for an exception.
        fix: Whether the run was a fix, whose accounting is then unmeasured.

    Returns:
        The result, marked as an execution failure when a command was refused.
    """
    refusal = command_refusal(tool)
    if refusal is None:
        return result
    # An npx integrity refusal found after the run: the tool ran, but its
    # results cannot be trusted (#3406, #3502). A failed npx install is
    # neither: the tool never ran (#3530).
    if isinstance(refusal, NpxInstallError):
        lead = f"npx could not install {result.name}"
    elif getattr(refusal, "after_run", False):
        lead = f"lintro cannot trust {result.name}'s results"
    else:
        lead = f"lintro refused to run {result.name}"
    return _refused_result(result, f"{lead}: {refusal}", fix=fix)


def _refused_result(result: ToolResult, reason: str, *, fix: bool) -> ToolResult:
    """Mark a result lintro refused, wholly or in part, as an execution failure.

    Args:
        result: The tool's result.
        reason: Why lintro refused; it leads the output.
        fix: Whether the run was a fix, whose accounting is then unmeasured.

    Returns:
        The result as an execution failure with incomplete findings, keeping
        the findings that really ran.
    """
    accounting: FixAccounting = (
        FixUnmeasured(reason=reason, initial=result.initial_issues_count)
        if fix
        else FixPartial()
    )
    outcome = ToolOutcome(
        execution=Checked(success=False, inspected_files=None),
        accounting=accounting,
        issues_count=result.issues_count,
    )
    prior = (result.output or "").strip()
    return replace(
        result.with_outcome(outcome),
        execution_failed=True,
        findings_complete=False,
        inspected_paths=None,
        # Keep the tool's own text even when it already echoes the refusal:
        # it may carry real findings from the invocations that did run.
        output=f"{reason}\n{prior}" if prior else reason,
    )


__all__ = ["command_refusal", "mark_refused_command", "refused_command_failure"]
