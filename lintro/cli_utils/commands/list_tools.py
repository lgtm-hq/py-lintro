"""List tools command implementation for lintro CLI.

This module provides the core logic for the 'list_tools' command.
"""

from __future__ import annotations

import json as json_lib
from collections.abc import Mapping
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lintro.enums.action import Action
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.registry import ToolRegistry
from lintro.tools import tool_manager
from lintro.utils.console import get_tool_emoji
from lintro.utils.unified_config import get_tool_priority, is_tool_injectable

if TYPE_CHECKING:
    from lintro.tools.core.snapshots import ToolSnapshot


def _resolve_conflicts(
    plugin: BaseToolPlugin,
    available_tools: dict[str, BaseToolPlugin],
) -> list[str]:
    """Resolve conflict names for a tool.

    Args:
        plugin: The plugin instance.
        available_tools: Dictionary of available tools.

    Returns:
        List of conflict tool names.
    """
    conflict_names: list[str] = []
    conflicts_with = plugin.definition.conflicts_with
    if conflicts_with:
        for conflict in conflicts_with:
            conflict_lower = conflict.lower()
            if conflict_lower in available_tools:
                conflict_names.append(conflict_lower)
    return conflict_names


@click.command("list-tools")
@click.option(
    "--output",
    type=click.Path(),
    help="Output file path for writing results",
)
@click.option(
    "--show-conflicts",
    is_flag=True,
    help="Show potential conflicts between tools",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output tool list as JSON",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output including file extensions and patterns",
)
def list_tools_command(
    output: str | None,
    show_conflicts: bool,
    json_output: bool,
    verbose: bool,
) -> None:
    """List all available tools and their configurations.

    Args:
        output: Path to output file for writing results.
        show_conflicts: Whether to show potential conflicts between tools.
        json_output: Output tool list as JSON.
        verbose: Show verbose output including file extensions and patterns.
    """
    list_tools(
        output=output,
        show_conflicts=show_conflicts,
        json_output=json_output,
        verbose=verbose,
    )


def list_tools(
    output: str | None,
    show_conflicts: bool,
    json_output: bool = False,
    verbose: bool = False,
) -> None:
    """List all available tools.

    Args:
        output: Output file path.
        show_conflicts: Whether to show potential conflicts between tools.
        json_output: Output tool list as JSON.
        verbose: Show verbose output including file extensions and patterns.
    """
    from lintro.tools.core.snapshots import probe_all_tools

    available_tools = tool_manager.get_all_tools()
    check_tools = tool_manager.get_check_tools()
    fix_tools = tool_manager.get_fix_tools()
    snapshots = probe_all_tools(tool_names=list(available_tools.keys()))

    # JSON output mode
    if json_output:
        tools_data: dict[str, dict[str, object]] = {}
        for tool_name, plugin in available_tools.items():
            capabilities: list[str] = []
            if tool_name in check_tools:
                capabilities.append("check")
            if tool_name in fix_tools:
                capabilities.append("fix")

            snap = snapshots.get(tool_name.lower())
            tool_info: dict[str, object] = {
                "description": plugin.definition.description,
                "capabilities": capabilities,
                "priority": get_tool_priority(tool_name),
                "syncable": is_tool_injectable(tool_name),
                "origin": ToolRegistry.get_origin(tool_name),
                "available": bool(snap.available) if snap else False,
                "version": snap.version if snap else None,
                "probe_error": snap.probe_error if snap else None,
            }
            if snap is not None:
                tool_info["runtime_capabilities"] = snap.capabilities.to_dict()
                if not snap.available:
                    tool_info["status"] = "unavailable"
                    tool_info["remediation_hint"] = snap.remediation_hint

            # Only include file_patterns in verbose mode (consistent with table output)
            if verbose:
                tool_info["file_patterns"] = plugin.definition.file_patterns

            if show_conflicts:
                conflict_names = _resolve_conflicts(
                    plugin=plugin,
                    available_tools=available_tools,
                )
                tool_info["conflicts_with"] = conflict_names

            tools_data[tool_name] = tool_info

        click.echo(json_lib.dumps(tools_data, indent=2))
        return

    console = Console()

    # Header panel
    console.print(
        Panel.fit(
            "[bold cyan]🔧 Available Tools[/bold cyan]",
            border_style="cyan",
        ),
    )
    console.print()

    # Main tools table
    table = Table(title="Tool Details")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Status", style="yellow")
    table.add_column("Description", style="white", max_width=40)
    table.add_column("Capabilities", style="green")
    table.add_column("Priority", justify="center", style="yellow")
    table.add_column("Type", style="magenta")
    table.add_column("Origin", style="blue")

    if verbose:
        table.add_column("Extensions", style="dim", max_width=30)

    if show_conflicts:
        table.add_column("Conflicts", style="red")

    for tool_name, plugin in available_tools.items():
        tool_description = plugin.definition.description
        emoji = get_tool_emoji(tool_name)
        snap = snapshots.get(tool_name.lower())
        if snap is None:
            status_display = "unknown"
        elif snap.available:
            version = snap.version or "?"
            status_display = f"ok ({version})"
        else:
            status_display = "unavailable"

        # Capabilities
        tool_capabilities: list[str] = []
        if tool_name in check_tools:
            tool_capabilities.append("check")
        if tool_name in fix_tools:
            tool_capabilities.append("fix")
        caps_display = ", ".join(tool_capabilities) if tool_capabilities else "-"

        # Priority and type
        priority = get_tool_priority(tool_name)
        injectable = is_tool_injectable(tool_name)
        tool_type = "Syncable" if injectable else "Native only"

        origin = ToolRegistry.get_origin(tool_name)

        row = [
            f"{emoji} {tool_name}",
            status_display,
            tool_description,
            caps_display,
            str(priority),
            tool_type,
            origin,
        ]

        # File patterns (verbose mode)
        if verbose:
            patterns = plugin.definition.file_patterns or []
            pat_display = ", ".join(patterns[:5])
            if len(patterns) > 5:
                pat_display += f" (+{len(patterns) - 5})"
            row.append(pat_display if patterns else "-")

        # Conflicts
        if show_conflicts:
            conflict_names = _resolve_conflicts(
                plugin=plugin,
                available_tools=available_tools,
            )
            row.append(", ".join(conflict_names) if conflict_names else "-")

        table.add_row(*row)

    console.print(table)
    console.print()

    # Summary table
    summary_table = Table(
        title="Summary",
        show_header=False,
        box=None,
    )
    summary_table.add_column("Metric", style="cyan", width=20)
    summary_table.add_column("Count", style="yellow", justify="right")

    available_count = sum(1 for s in snapshots.values() if s.available)
    summary_table.add_row("📊 Total tools", str(len(available_tools)))
    summary_table.add_row("✅ Runtime available", str(available_count))
    summary_table.add_row("🔍 Check tools", str(len(check_tools)))
    summary_table.add_row("🔧 Fix tools", str(len(fix_tools)))

    console.print(summary_table)

    # Write to file if specified
    if output:
        try:
            # For file output, use plain text format
            output_lines = _generate_plain_text_output(
                available_tools=available_tools,
                check_tools=check_tools,
                fix_tools=fix_tools,
                show_conflicts=show_conflicts,
                snapshots=snapshots,
            )
            with open(output, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines) + "\n")
            console.print()
            console.print(f"[green]✅ Output written to: {output}[/green]")
        except OSError as e:
            console.print(f"[red]Error writing to file {output}: {e}[/red]")


def _generate_plain_text_output(
    available_tools: dict[str, BaseToolPlugin],
    check_tools: dict[str, BaseToolPlugin],
    fix_tools: dict[str, BaseToolPlugin],
    show_conflicts: bool,
    snapshots: Mapping[str, ToolSnapshot] | None = None,
) -> list[str]:
    """Generate plain text output for file writing.

    Args:
        available_tools: Dictionary of available tools.
        check_tools: Dictionary of check-capable tools.
        fix_tools: Dictionary of fix-capable tools.
        show_conflicts: Whether to include conflict information.
        snapshots: Optional capability snapshots keyed by tool name.

    Returns:
        List of output lines.
    """
    output_lines: list[str] = []
    border = "=" * 70
    snapshots = snapshots or {}

    output_lines.append(border)
    output_lines.append("Available Tools")
    output_lines.append(border)
    output_lines.append("")

    for tool_name, plugin in available_tools.items():
        tool_description = plugin.definition.description
        emoji = get_tool_emoji(tool_name)

        capabilities: list[str] = []
        if tool_name in check_tools:
            capabilities.append(Action.CHECK.value)
        if tool_name in fix_tools:
            capabilities.append(Action.FIX.value)

        capabilities_display = ", ".join(capabilities) if capabilities else "-"
        snap = snapshots.get(tool_name.lower())
        if snap is None:
            runtime_status = "unknown"
        elif getattr(snap, "available", False):
            runtime_status = f"ok ({getattr(snap, 'version', None) or '?'})"
        else:
            runtime_status = "unavailable"

        output_lines.append(f"{emoji} {tool_name}: {tool_description}")
        output_lines.append(f"  Status: {runtime_status}")
        output_lines.append(f"  Capabilities: {capabilities_display}")
        output_lines.append(f"  Origin: {ToolRegistry.get_origin(tool_name)}")

        if show_conflicts:
            conflict_names = _resolve_conflicts(
                plugin=plugin,
                available_tools=available_tools,
            )
            if conflict_names:
                output_lines.append(f"  Conflicts with: {', '.join(conflict_names)}")

        output_lines.append("")

    summary_border = "-" * 70
    output_lines.append(summary_border)
    output_lines.append(f"Total tools: {len(available_tools)}")
    output_lines.append(f"Check tools: {len(check_tools)}")
    output_lines.append(f"Fix tools: {len(fix_tools)}")
    output_lines.append(summary_border)

    return output_lines
