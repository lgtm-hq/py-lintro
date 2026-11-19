"""Shared rendering for install/upgrade execution results.

``lintro install`` and ``lintro doctor --fix`` both execute the same plan and
must report it the same way: one line per planned action, numbered, so it is
unambiguous that later actions still ran after an earlier failure or timeout.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape

from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_plan import InstallPlan, InstallResult
from lintro.tools.core.manifest_models import ManifestTool

_OUTCOME_STYLES: dict[InstallOutcome, str] = {
    InstallOutcome.SUCCESS: "green",
    InstallOutcome.NOT_DISCOVERABLE: "yellow",
    InstallOutcome.STILL_OUTDATED: "yellow",
    InstallOutcome.FAILED: "red",
    InstallOutcome.TIMED_OUT: "yellow",
    InstallOutcome.MANUAL_BLOCKED: "yellow",
}

_SUMMARY_ORDER: tuple[InstallOutcome, ...] = (
    InstallOutcome.SUCCESS,
    InstallOutcome.NOT_DISCOVERABLE,
    InstallOutcome.STILL_OUTDATED,
    InstallOutcome.FAILED,
    InstallOutcome.TIMED_OUT,
    InstallOutcome.MANUAL_BLOCKED,
)

_SUMMARY_LABELS: dict[InstallOutcome, str] = {
    InstallOutcome.SUCCESS: "installed",
    InstallOutcome.NOT_DISCOVERABLE: "not discoverable",
    InstallOutcome.STILL_OUTDATED: "still below minimum version",
    InstallOutcome.FAILED: "failed",
    InstallOutcome.TIMED_OUT: "timed out",
    InstallOutcome.MANUAL_BLOCKED: "manual action required",
}


def render_planned_install(
    console: Console,
    plan: InstallPlan,
    tool: ManifestTool,
    command: str,
) -> None:
    """Render one planned install and say when it installs the whole project.

    A project-wide install (``npm install``) fetches every declared
    dependency and runs its install scripts. Asking for one tool must not do
    that unannounced, through ``lintro install`` or ``lintro doctor --fix``
    (#2045).

    Args:
        console: Console to print to.
        plan: Plan the install belongs to.
        tool: Tool the command was planned for.
        command: The planned command.
    """
    console.print(f"    {tool.name:<20} [dim]{escape(command)}[/dim]")
    root = plan.project_installs.get(tool.name)
    if root is None:
        return
    console.print(
        f"      [yellow]installs the whole project's dependencies "
        f"(`{escape(command)}` in {escape(str(root))}), not only "
        f"{tool.name}[/yellow]",
        highlight=False,
    )


def render_manual_installs(console: Console, plan: InstallPlan) -> None:
    """Render the tools the plan leaves to the user, with each hint.

    ``lintro install`` shows these in its plan and ``lintro doctor --fix``
    after its install results; neither runs anything for them, so the hint
    is the only place the user learns what to do (#2045).

    Args:
        console: Console to print to.
        plan: Plan whose manual list is rendered.
    """
    if not plan.manual:
        return
    console.print(
        f"  [yellow]Manual installation required ({len(plan.manual)}):[/yellow]",
    )
    for tool, hint in plan.manual:
        console.print(f"    {tool.name:<20}")
        for line in hint.split("\n"):
            console.print(f"      [dim]{escape(line)}[/dim]", highlight=False)


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
        if result.command:
            console.print(f"      [dim]command: {result.command}[/dim]")
        if not result.success:
            console.print(f"      [dim]{result.message}[/dim]")


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
        identical command cannot fix. Includes ``NOT_DISCOVERABLE`` and
        ``STILL_OUTDATED``. The list is consumed as per-process
        ``known_invalid``: within one run a failed remedy is never
        re-suggested; a fresh run gets one fresh attempt. There is no
        state file.
    """
    return [
        result.tool.name
        for result in results
        if not result.success and not result.outcome.is_retryable
    ]
