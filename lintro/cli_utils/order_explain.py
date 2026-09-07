"""Rendering for the shadow-mode execution-order diff (#1741).

Presentation only. The derivation lives in
:mod:`lintro.tools.core.scheduler`; this module turns its report into the
lines printed by ``lintro check --explain-order``, ``lintro fmt
--explain-order`` and the ``lintro doctor`` order section.

Nothing here influences execution: ``--explain-order`` prints the diff and
returns instead of running tools, and the doctor section is informational.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import click
from rich.text import Text

from lintro.tools.core.scheduler import build_shadow_report

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console

    from lintro.tools.core.scheduler import (
        OrderCycle,
        OrderDifference,
        OrderShadowReport,
    )

#: Header printed above every explanation.
EXPLAIN_HEADER: str = "Execution order (shadow mode)"

#: Repeated wherever the diff is shown, so nobody reads it as live behaviour.
SHADOW_NOTE: str = (
    "Reporting only: the scalar-priority order is still the one that runs."
)

#: Cap on the per-difference pattern list so a `*` claim cannot flood output.
MAX_EDGES_PER_DIFFERENCE: int = 5

#: Cap on the differences listed in the compact doctor section.
MAX_DOCTOR_DIFFERENCES: int = 5


def _format_order(label: str, names: Sequence[str]) -> list[str]:
    """Render one numbered order listing.

    Args:
        label: Section label (e.g. ``"Current (scalar priority)"``).
        names: Tool names in order.

    Returns:
        Lines for the listing, including its label.
    """
    lines = [f"  {label}:"]
    if not names:
        lines.append("    (no tools selected)")
        return lines
    width = len(str(len(names)))
    lines.extend(
        f"    {index:>{width}}. {name}" for index, name in enumerate(names, start=1)
    )
    return lines


def _format_difference(difference: OrderDifference) -> list[str]:
    """Render one disagreement and the edges that produced it.

    Args:
        difference: The disagreement to render.

    Returns:
        Lines describing the pair and the claiming patterns.
    """
    lines = [
        f"    {difference.before} should run before {difference.after} "
        f"(current order runs {difference.after} first)",
    ]
    shown = difference.edges[:MAX_EDGES_PER_DIFFERENCE]
    lines.extend(f"      {edge.reason}" for edge in shown)
    hidden = len(difference.edges) - len(shown)
    if hidden > 0:
        lines.append(f"      ... and {hidden} more pattern(s)")
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


def format_shadow_report(report: OrderShadowReport) -> list[str]:
    """Render the full shadow-order explanation.

    Args:
        report: Report produced by
            :func:`lintro.tools.core.scheduler.build_shadow_report`.

    Returns:
        Plain-text lines, ready to print one per line.
    """
    lines = [EXPLAIN_HEADER, f"  {SHADOW_NOTE}", ""]
    lines.extend(_format_order("Current (scalar priority)", report.current))
    lines.append("")
    lines.extend(_format_order("Derived (claims)", report.derived))
    lines.append("")

    if report.differences:
        lines.append(f"  Differences ({len(report.differences)}):")
        for difference in report.differences:
            lines.extend(_format_difference(difference))
    else:
        lines.append("  Differences (0): the current order satisfies every")
        lines.append("  derived constraint.")

    lines.append("")
    if report.cycles:
        lines.append(f"  Cycles ({len(report.cycles)}):")
        for cycle in report.cycles:
            lines.extend(_format_cycle(cycle))
    else:
        lines.append("  Cycles (0): the derived graph is a DAG.")
    return lines


def format_doctor_order_section(report: OrderShadowReport) -> list[str]:
    """Render the compact ``lintro doctor`` order section.

    Args:
        report: Report produced by
            :func:`lintro.tools.core.scheduler.build_shadow_report`.

    Returns:
        Plain-text lines for the doctor section.
    """
    lines = [
        "  Execution order (shadow)",
        f"    {SHADOW_NOTE}",
        f"    tools: {len(report.current)}"
        f"  differences: {len(report.differences)}"
        f"  cycles: {len(report.cycles)}",
    ]
    shown = report.differences[:MAX_DOCTOR_DIFFERENCES]
    lines.extend(
        f"    {difference.before} before {difference.after} "
        f"({difference.edges[0].pattern})"
        for difference in shown
    )
    hidden = len(report.differences) - len(shown)
    if hidden > 0:
        lines.append(f"    ... and {hidden} more difference(s)")
    lines.append("    Run 'lintro check --explain-order' for the full diff.")
    return lines


def explain_order_lines(
    tools: str | None,
    action: str,
    paths: Sequence[str],
    *,
    ignore_conflicts: bool = False,
) -> list[str]:
    """Build the ``--explain-order`` output for a would-be run.

    Tool selection reuses the same resolution the run itself performs, so the
    ``current`` order shown is exactly the order that invocation would have
    executed.

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
    return format_shadow_report(build_shadow_report(selection.to_run))


def emit_order_explanation(
    *,
    tools: str | None,
    action: str,
    paths: Sequence[str],
    ignore_conflicts: bool = False,
) -> NoReturn:
    """Print the ``--explain-order`` diff and exit without running any tool.

    Args:
        tools: ``--tools`` value, or None for the configured/detected set.
        action: ``"check"`` or ``"fmt"``.
        paths: Paths the run would have scanned.
        ignore_conflicts: Mirror of the run's ``--ignore-conflicts``.

    Raises:
        SystemExit: Always. ``0`` once the diff is printed, ``1`` when the
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
    except (ValueError, OSError):  # pragma: no cover - defensive
        return []
    return format_doctor_order_section(build_shadow_report(selection.to_run))


def render_doctor_order_section(console: Console) -> None:
    """Print the shadow-mode execution-order section of ``lintro doctor``.

    Informational only: it reports how the claims-derived order differs from
    the scalar-priority order that actually runs, and never influences a
    doctor exit code.

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
