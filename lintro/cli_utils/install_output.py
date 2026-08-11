"""Shared rendering for install/upgrade execution results.

``lintro install``, ``lintro setup`` and ``lintro doctor --fix`` all execute
the same plan and must report it the same way: one line per planned action,
numbered, so it is unambiguous that later actions still ran after an earlier
failure or timeout.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console

from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_plan import InstallResult

_OUTCOME_STYLES: dict[InstallOutcome, str] = {
    InstallOutcome.SUCCESS: "green",
    InstallOutcome.NOT_DISCOVERABLE: "yellow",
    InstallOutcome.FAILED: "red",
    InstallOutcome.TIMED_OUT: "yellow",
    InstallOutcome.MANUAL_BLOCKED: "yellow",
}

_SUMMARY_ORDER: tuple[InstallOutcome, ...] = (
    InstallOutcome.SUCCESS,
    InstallOutcome.NOT_DISCOVERABLE,
    InstallOutcome.FAILED,
    InstallOutcome.TIMED_OUT,
    InstallOutcome.MANUAL_BLOCKED,
)

_SUMMARY_LABELS: dict[InstallOutcome, str] = {
    InstallOutcome.SUCCESS: "installed",
    InstallOutcome.NOT_DISCOVERABLE: "installed but not on PATH",
    InstallOutcome.FAILED: "failed",
    InstallOutcome.TIMED_OUT: "timed out",
    InstallOutcome.MANUAL_BLOCKED: "manual action required",
}


def render_install_results(
    console: Console,
    results: Sequence[InstallResult],
) -> None:
    """Render one numbered line per executed action.

    Args:
        console: Console to print to.
        results: Results returned by ``ToolInstaller.execute``.
    """
    for result in results:
        style = _OUTCOME_STYLES[result.outcome]
        prefix = (
            f"[{result.step}/{result.total_steps}] "
            if result.step and result.total_steps
            else ""
        )
        line = (
            f"  {prefix}[{style}]{result.outcome.label}[/{style}]  {result.tool.name} "
            f"[dim]({result.duration_seconds:.1f}s)[/dim]"
        )
        console.print(line)
        if not result.success:
            console.print(f"      [dim]{result.message}[/dim]")
            if result.command:
                console.print(f"      [dim]command: {result.command}[/dim]")


def render_outcome_summary(
    console: Console,
    results: Sequence[InstallResult],
) -> None:
    """Render a per-outcome summary of an executed plan.

    Args:
        console: Console to print to.
        results: Results returned by ``ToolInstaller.execute``.
    """
    if not results:
        return
    counts = count_outcomes(results)
    parts = [
        f"{counts[outcome]} {_SUMMARY_LABELS[outcome]}"
        for outcome in _SUMMARY_ORDER
        if counts[outcome]
    ]
    console.print(
        f"  [bold]Attempted {len(results)} action(s):[/bold] {', '.join(parts)}",
    )


def count_outcomes(
    results: Sequence[InstallResult],
) -> dict[InstallOutcome, int]:
    """Count results per outcome.

    Args:
        results: Results returned by ``ToolInstaller.execute``.

    Returns:
        Mapping of every outcome to its count (zero when absent).
    """
    counts = dict.fromkeys(InstallOutcome, 0)
    for result in results:
        counts[result.outcome] += 1
    return counts


def unresolved_tool_names(
    results: Sequence[InstallResult],
) -> list[str]:
    """List tools whose command must not be suggested again unchanged.

    Args:
        results: Results returned by ``ToolInstaller.execute``.

    Returns:
        Names of tools whose action failed in a way that re-running the
        identical command cannot fix.
    """
    return [
        result.tool.name
        for result in results
        if not result.success and not result.outcome.is_retryable
    ]
