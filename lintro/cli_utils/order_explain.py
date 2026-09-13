"""Rendering for the derived execution order (#1742).

Presentation only. The derivation lives in
:mod:`lintro.tools.core.scheduler`; this module turns its report into the
lines printed by ``lintro check --explain-order``, ``lintro fmt
--explain-order`` and the ``lintro doctor`` order section.

Since #1742 the derived order *is* the order that runs, so what these
surfaces print is not a preview of a future scheduler: it is an explanation
of the run that would have happened, tool by tool, with the claim that put
each tool where it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import click
from rich.text import Text

from lintro.tools.core.scheduler import build_order_report

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console

    from lintro.tools.core.scheduler import (
        DerivedOrder,
        FormatDemotion,
        OrderCycle,
        OrderEdge,
    )

#: Header printed above every explanation.
EXPLAIN_HEADER: str = "Execution order (derived from tool claims)"

#: Repeated wherever the order is shown, so its authority is unambiguous.
DERIVED_NOTE: str = (
    "This is the order that runs. It is derived from what each tool claims "
    "to touch, not configured."
)

#: Cap on the per-tool constraint list so a `*` claim cannot flood output.
MAX_EDGES_PER_TOOL: int = 5

#: Cap on the tools listed with their constraints in the doctor section.
MAX_DOCTOR_CONSTRAINTS: int = 5

#: Cap on the format-owner demotions listed in the doctor section.
MAX_DOCTOR_DEMOTIONS: int = 3


def _predecessors(report: DerivedOrder) -> dict[str, list[OrderEdge]]:
    """Group derived edges by the tool they constrain.

    Args:
        report: Result of
            :func:`lintro.tools.core.scheduler.build_order_report`.

    Returns:
        Mapping of tool name to the edges requiring another tool first.
    """
    grouped: dict[str, list[OrderEdge]] = {name: [] for name in report.tools}
    for edge in report.edges:
        grouped.setdefault(edge.after, []).append(edge)
    return grouped


def _format_tool(
    index: int,
    width: int,
    name: str,
    edges: Sequence[OrderEdge],
) -> list[str]:
    """Render one tool's position and the constraints that put it there.

    Args:
        index: 1-based position in the order.
        width: Field width for the position number.
        name: Tool name.
        edges: Derived edges requiring another tool to run before this one.

    Returns:
        Lines for this tool.
    """
    lines = [f"    {index:>{width}}. {name}"]
    if not edges:
        lines.append("        (unconstrained; alphabetical tiebreak)")
        return lines
    shown = edges[:MAX_EDGES_PER_TOOL]
    lines.extend(f"        after {edge.before} — {edge.reason}" for edge in shown)
    hidden = len(edges) - len(shown)
    if hidden > 0:
        lines.append(f"        ... and {hidden} more constraint(s)")
    return lines


def _format_cycle(cycle: OrderCycle) -> list[str]:
    """Render one derived cycle.

    Args:
        cycle: The cycle to render.

    Returns:
        Lines naming the tools and the patterns that closed the cycle.
    """
    return [
        f"    {' <-> '.join(cycle.tools)}",
        f"      patterns: {', '.join(cycle.patterns)}",
    ]


def _format_demotion_record(record: FormatDemotion) -> str:
    """Render one demotion as the compact line doctor and init both print.

    The full ``--explain-order`` listing uses ``record.reason`` instead, which
    the scheduler composes and which also names the override key.

    Args:
        record: The demotion to render.

    Returns:
        A single line naming the scope, the owner, the demoted tool and why.
    """
    return (
        f"{record.scope}: {record.winner} owns FORMAT, "
        f"{record.loser} demoted ({record.rule})"
    )


def _format_demotions(demotions: Sequence[FormatDemotion]) -> list[str]:
    """Render the format-owner decisions the scheduler made (#1744).

    Args:
        demotions: Demotions recorded on the derived order.

    Returns:
        Lines naming the winner, the loser, the scope and the reason, or a
        single line saying nothing contended.
    """
    if not demotions:
        return ["  Format ownership (0): no two tools contend for one scope."]
    lines = [f"  Format ownership ({len(demotions)}):"]
    lines.extend(f"    {record.reason}" for record in demotions)
    return lines


def format_order_report(report: DerivedOrder) -> list[str]:
    """Render the full execution-order explanation.

    Args:
        report: Report produced by
            :func:`lintro.tools.core.scheduler.build_order_report`.

    Returns:
        Plain-text lines, ready to print one per line.
    """
    lines = [EXPLAIN_HEADER, f"  {DERIVED_NOTE}", ""]
    if not report.tools:
        lines.append("    (no tools selected)")
        return lines

    predecessors = _predecessors(report)
    width = len(str(len(report.tools)))
    for index, name in enumerate(report.tools, start=1):
        lines.extend(_format_tool(index, width, name, predecessors[name]))

    lines.append("")
    if report.cycles:
        lines.append(f"  Cycles ({len(report.cycles)}):")
        for cycle in report.cycles:
            lines.extend(_format_cycle(cycle))
        lines.append(
            "  Cycles are broken alphabetically so a run stays deterministic.",
        )
    else:
        lines.append("  Cycles (0): the derived graph is a DAG.")
    lines.append("")
    lines.extend(_format_demotions(report.demotions))
    return lines


def format_doctor_order_section(report: DerivedOrder) -> list[str]:
    """Render the compact ``lintro doctor`` order section.

    Args:
        report: Report produced by
            :func:`lintro.tools.core.scheduler.build_order_report`.

    Returns:
        Plain-text lines for the doctor section.
    """
    predecessors = _predecessors(report)
    constrained = [name for name in report.tools if predecessors[name]]
    lines = [
        "  Execution order (derived)",
        f"    {DERIVED_NOTE}",
        f"    tools: {len(report.tools)}"
        f"  constraints: {len(report.edges)}"
        f"  cycles: {len(report.cycles)}"
        f"  format demotions: {len(report.demotions)}",
    ]
    shown = constrained[:MAX_DOCTOR_CONSTRAINTS]
    lines.extend(
        f"    {name} after {predecessors[name][0].before} "
        f"({predecessors[name][0].pattern})"
        for name in shown
    )
    hidden = len(constrained) - len(shown)
    if hidden > 0:
        lines.append(f"    ... and {hidden} more constrained tool(s)")
    shown_demotions = report.demotions[:MAX_DOCTOR_DEMOTIONS]
    lines.extend(f"    {_format_demotion_record(record)}" for record in shown_demotions)
    hidden_demotions = len(report.demotions) - len(shown_demotions)
    if hidden_demotions > 0:
        lines.append(f"    ... and {hidden_demotions} more demotion(s)")
    lines.append("    Run 'lintro check --explain-order' for the full order.")
    return lines


def format_ownership_notice(tool_names: Sequence[str]) -> list[str]:
    """Report the format-owner decisions a selection implies (#1744, #2606).

    Used by ``lintro init`` so a generated config says up front which tool
    will own formatting where two of them contend, and which config key
    changes it. Resolution itself happens on every run, not here.

    Args:
        tool_names: Tools the config enables.

    Returns:
        Plain-text lines, or an empty list when nothing contends or the
        scheduler could not answer — an advisory notice must not fail init.
    """
    try:
        report = build_order_report(tool_names)
    except (ValueError, OSError):
        return []
    if not report.demotions:
        return []
    lines = ["  Format ownership:"]
    lines.extend(
        f"    {_format_demotion_record(record)}" for record in report.demotions
    )
    lines.append("    Change it with execution.precedence in your config.")
    return lines


def explain_order_lines(
    tools: str | None,
    action: str,
    paths: Sequence[str],
    *,
    ignore_conflicts: bool = False,
) -> list[str]:
    """Build the ``--explain-order`` output for a would-be run.

    Tool selection reuses the same resolution the run itself performs, and the
    order comes from the same scheduler the run uses, so what is printed is
    exactly what that invocation would have executed.

    Args:
        tools: ``--tools`` value, or None for the configured/detected set.
        action: ``"check"`` or ``"fmt"``.
        paths: Paths the run would scan, used for language detection.
        ignore_conflicts: Mirror of the run's ``--ignore-conflicts``.

    Returns:
        Plain-text lines, ready to print one per line.
    """
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    selection = get_tools_to_run(
        tools,
        action,
        ignore_conflicts=ignore_conflicts,
        scan_roots=list(paths),
    )
    # Explain what *this* invocation would do: a mutating action derives
    # write-conflict edges, a read-only one does not, and overlap is decided
    # from the paths the run would have scanned.
    return format_order_report(
        build_order_report(
            selection.to_run,
            paths=list(paths) or None,
            write_conflicts=action != "check",
        ),
    )


def emit_order_explanation(
    *,
    tools: str | None,
    action: str,
    paths: Sequence[str],
    ignore_conflicts: bool = False,
) -> NoReturn:
    """Print the ``--explain-order`` output and exit without running any tool.

    Args:
        tools: ``--tools`` value, or None for the configured/detected set.
        action: ``"check"`` or ``"fmt"``.
        paths: Paths the run would have scanned.
        ignore_conflicts: Mirror of the run's ``--ignore-conflicts``.

    Raises:
        SystemExit: Always. ``0`` once the order is printed, ``1`` when the
            tool selection itself is invalid.
    """
    try:
        lines = explain_order_lines(
            tools,
            action,
            paths,
            ignore_conflicts=ignore_conflicts,
        )
    except ValueError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    for line in lines:
        click.echo(line)
    raise SystemExit(0)


def doctor_order_lines() -> list[str]:
    """Build the doctor order section for the workspace's default toolset.

    Returns:
        Plain-text lines for the doctor section, or an empty list when the
        toolset cannot be resolved (an unusable config must not break the
        rest of ``lintro doctor``).
    """
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    try:
        selection = get_tools_to_run(None, "check")
    except (ValueError, OSError):
        return []
    return format_doctor_order_section(build_order_report(selection.to_run))


def render_doctor_order_section(console: Console) -> None:
    """Print the derived execution-order section of ``lintro doctor``.

    Informational only: it reports the order the next run will use and never
    influences a doctor exit code.

    Args:
        console: Rich console to print to.
    """
    lines = doctor_order_lines()
    if not lines:
        return
    console.print()
    console.print(Text(f"  {lines[0].strip()}", style="bold"))
    for line in lines[1:]:
        console.print(Text(line, style="dim"))
