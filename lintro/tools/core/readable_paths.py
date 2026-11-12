"""Keep paths a tool would misread away from it (#3393).

Some tools expand the paths they are given instead of reading them
literally. Stylelint turns a backslash into ``/``; html-validate runs every
argument through ``fs.globSync``. A file whose name the tool misreads is
then silently dropped, or a different file is read or rewritten in its place.
Such paths are never passed to the tool. The rest still run, and the result
is an incomplete run that names the paths left uninspected.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, TypeVar

from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult


class PreparedFiles(Protocol):
    """The parts of a prepared execution context this module reads."""

    __dataclass_fields__: ClassVar[dict[str, Any]]

    @property
    def files(self) -> list[str]:
        """Absolute paths to the discovered files."""
        ...

    @property
    def rel_files(self) -> list[str]:
        """Paths relative to the execution directory, as passed to the tool."""
        ...

    @property
    def cwd(self) -> str:
        """Working directory for command execution."""
        ...


CtxT = TypeVar("CtxT", bound=PreparedFiles)


def unread_paths_reason(*, tool: str, paths: list[str], why: str) -> str:
    """Name the paths a tool was not run on, and why.

    Args:
        tool: Display name of the tool.
        paths: Relative paths withheld from the tool.
        why: How the tool would misread them.

    Returns:
        A one-line reason naming the count and the first path.
    """
    return (
        f"{tool} was not run on {len(paths)} file(s), such as {paths[0]!r}: "
        f"{why} Those files were not inspected."
    )


def with_unread_paths(*, result: ToolResult, reason: str) -> ToolResult:
    """Mark a run over the readable paths as incomplete.

    Findings, fix accounting and parse failures for the files the tool did
    read are kept. The run is an execution failure with unknown inspection,
    because some files were never inspected.

    Args:
        result: The result for the readable paths.
        reason: Why the other paths were withheld.

    Returns:
        A failed, incomplete result that names the reason first.
    """
    output = f"{reason}\n{result.output}" if (result.output or "").strip() else reason
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=False, inspected_files=None),
            accounting=result.outcome.accounting,
            issues_count=result.issues_count,
        ),
        name=result.name,
        output=output,
        issues=list(result.issues),
        initial_issues=list(result.initial_issues),
        cwd=result.cwd,
        parse_failures_count=result.parse_failures_count,
        execution_failed=True,
        findings_complete=False,
    )


def run_on_readable_paths(
    ctx: CtxT,
    *,
    readable: Callable[[str], bool],
    reason: Callable[[list[str]], str],
    run: Callable[[CtxT], ToolResult],
    unrun: Callable[[str, str], ToolResult],
) -> ToolResult:
    """Run a tool only on the paths it reads literally.

    The paths judged are the ones a tool passes: ``rel_files``, or
    ``files`` when no relative paths were computed.

    Args:
        ctx: The prepared execution context.
        readable: Whether the tool reads a passed path literally.
        reason: Builds the reason naming the withheld paths.
        run: Runs the tool on a context.
        unrun: Builds the failed result for a run that never started,
            from the reason and the working directory.

    Returns:
        The tool's own result when every path is readable. Otherwise an
        execution failure naming the withheld paths: the tool is not run at
        all when none is readable, and a run over the rest keeps its findings.
        A skipped or timed-out run is returned as it is.
    """
    passed = ctx.rel_files or ctx.files
    unread = [path for path in passed if not readable(path)]
    if not unread:
        return run(ctx)
    why = reason(unread)
    kept = [index for index, path in enumerate(passed) if readable(path)]
    if not kept:
        return unrun(why, ctx.cwd)
    result = run(
        dataclasses.replace(
            ctx,
            files=[ctx.files[index] for index in kept],
            rel_files=[ctx.rel_files[index] for index in kept] if ctx.rel_files else [],
        ),
    )
    if result.skipped or result.timed_out:
        return result
    if result.no_files:
        return unrun(why, ctx.cwd)
    return with_unread_paths(result=result, reason=why)
