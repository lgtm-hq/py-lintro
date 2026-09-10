"""List tools command implementation for lintro CLI.

This module provides the core logic for the 'list_tools' command.
"""

import json as json_lib

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lintro.enums.action import Action
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.registry import ToolRegistry
from lintro.tools import tool_manager
from lintro.tools.core.scheduler import derive_execution_order
from lintro.utils.console import get_tool_emoji
from lintro.utils.unified_config import is_tool_injectable

#: Capability label reported for advisory AI finders, which run under
#: ``lintro review`` instead of ``chk``/``fmt`` (#1308).
ADVISORY_CAPABILITY: str = "review"


def _tool_capabilities(
    *,
    tool_name: str,
    plugin: BaseToolPlugin,
    check_tools: dict[str, BaseToolPlugin],
    fix_tools: dict[str, BaseToolPlugin],
) -> list[str]:
    """Resolve the verbs a tool can be invoked with.

    Advisory AI finders report ``review`` rather than ``check``: they are
    excluded from ``lintro chk`` so their nondeterministic findings never
    gate deterministic checks or their issue counts (#1308).

    Args:
        tool_name: Registered tool name.
        plugin: The plugin instance.
        check_tools: Tools that support checking.
        fix_tools: Tools that support fixing.

    Returns:
        Capability labels in display order.
    """
    if plugin.definition.is_advisory:
        return [ADVISORY_CAPABILITY]
    capabilities: list[str] = []
    if tool_name in check_tools:
        capabilities.append(Action.CHECK.value)
    if tool_name in fix_tools:
        capabilities.append(Action.FIX.value)
    return capabilities


@click.command("list-tools")
@click.option(
    "--output",
    type=click.Path(),
    help="Output file path for writing results",
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
    json_output: bool,
    verbose: bool,
) -> None:
    """List all available tools and their configurations.

    \u000c

    Args:
        output: Path to output file for writing results.
        json_output: Output tool list as JSON.
        verbose: Show verbose output including file extensions and patterns.
    """
    list_tools(
        output=output,
        json_output=json_output,
        verbose=verbose,
    )


def list_tools(
    output: str | None,
    json_output: bool = False,
    verbose: bool = False,
) -> None:
    """List all available tools.

    Args:
        output: Output file path.
        json_output: Output tool list as JSON.
        verbose: Show verbose output including file extensions and patterns.
    """
    available_tools = tool_manager.get_all_tools()
    check_tools = tool_manager.get_check_tools()
    fix_tools = tool_manager.get_fix_tools()
    # Position in the derived execution order, so list-tools reports the same
    # ordering authority as every other surface (#1742).
    order_positions = {
        name: index
        for index, name in enumerate(
            derive_execution_order(list(available_tools)),
            start=1,
        )
    }

    # JSON output mode
    if json_output:
        tools_data: dict[str, dict[str, object]] = {}
        for tool_name, plugin in available_tools.items():
            capabilities = _tool_capabilities(
                tool_name=tool_name,
                plugin=plugin,
                check_tools=check_tools,
                fix_tools=fix_tools,
            )

            tool_info: dict[str, object] = {
                "description": plugin.definition.description,
                "capabilities": capabilities,
                "execution_class": plugin.definition.execution_class.value,
                "position": order_positions[tool_name],
                "syncable": is_tool_injectable(tool_name),
                "origin": ToolRegistry.get_origin(tool_name),
            }

            # Only include file_patterns in verbose mode (consistent with table output)
            if verbose:
                tool_info["file_patterns"] = plugin.definition.file_patterns

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
    table.add_column("Description", style="white", max_width=40)
    table.add_column("Capabilities", style="green")
    table.add_column("Order", justify="center", style="yellow")
    table.add_column("Type", style="magenta")
    table.add_column("Origin", style="blue")

    if verbose:
        table.add_column("Extensions", style="dim", max_width=30)

    for tool_name, plugin in available_tools.items():
        tool_description = plugin.definition.description
        emoji = get_tool_emoji(tool_name)

        # Capabilities
        tool_capabilities = _tool_capabilities(
            tool_name=tool_name,
            plugin=plugin,
            check_tools=check_tools,
            fix_tools=fix_tools,
        )
        caps_display = ", ".join(tool_capabilities) if tool_capabilities else "-"

        # Derived execution position and type
        position = order_positions[tool_name]
        injectable = is_tool_injectable(tool_name)
        tool_type = "Syncable" if injectable else "Native only"

        origin = ToolRegistry.get_origin(tool_name)

        row = [
            f"{emoji} {tool_name}",
            tool_description,
            caps_display,
            str(position),
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

    summary_table.add_row("📊 Total tools", str(len(available_tools)))
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
) -> list[str]:
    """Generate plain text output for file writing.

    Args:
        available_tools: Dictionary of available tools.
        check_tools: Dictionary of check-capable tools.
        fix_tools: Dictionary of fix-capable tools.

    Returns:
        List of output lines.
    """
    output_lines: list[str] = []
    border = "=" * 70

    output_lines.append(border)
    output_lines.append("Available Tools")
    output_lines.append(border)
    output_lines.append("")

    for tool_name, plugin in available_tools.items():
        tool_description = plugin.definition.description
        emoji = get_tool_emoji(tool_name)

        capabilities = _tool_capabilities(
            tool_name=tool_name,
            plugin=plugin,
            check_tools=check_tools,
            fix_tools=fix_tools,
        )

        capabilities_display = ", ".join(capabilities) if capabilities else "-"

        output_lines.append(f"{emoji} {tool_name}: {tool_description}")
        output_lines.append(f"  Capabilities: {capabilities_display}")
        output_lines.append(f"  Origin: {ToolRegistry.get_origin(tool_name)}")

        output_lines.append("")

    summary_border = "-" * 70
    output_lines.append(summary_border)
    # Advisory finders never run under chk/fmt, so they are counted on their
    # own line rather than inflating the check-tool total (#1308).
    advisory_names = {
        name
        for name, plugin in available_tools.items()
        if plugin.definition.is_advisory
    }
    output_lines.append(f"Total tools: {len(available_tools)}")
    output_lines.append(
        f"Check tools: {len(set(check_tools) - advisory_names)}",
    )
    output_lines.append(f"Fix tools: {len(set(fix_tools) - advisory_names)}")
    output_lines.append(f"Advisory tools: {len(advisory_names)}")
    output_lines.append(summary_border)

    return output_lines
