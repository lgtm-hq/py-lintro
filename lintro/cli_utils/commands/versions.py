"""Versions command for displaying tool version information."""

import json

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from lintro.tools.core.update_channels import format_advisory_line
from lintro.tools.core.version_requirements import get_all_tool_versions


@click.command()
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed version information including install hints.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output version information as JSON.",
)
def versions_command(verbose: bool, json_output: bool) -> None:
    """Display version information for all supported tools.

    Shows each tool's current version, minimum required version, and status.
    Use --verbose to see installation hints for tools that don't meet requirements.

    Args:
        verbose: Show detailed version information including install hints.
        json_output: Output version information as JSON.
    """
    tool_versions = get_all_tool_versions()

    # JSON output mode
    if json_output:
        output: dict[str, dict[str, object]] = {}
        for tool_name, version_info in sorted(tool_versions.items()):
            entry: dict[str, object] = {
                "current_version": version_info.current_version,
                "min_version": version_info.min_version,
                "recommended_version": version_info.recommended_version,
                "version_check_passed": version_info.version_check_passed,
                "below_recommended": version_info.below_recommended,
                "error_message": version_info.error_message,
                "install_hint": version_info.install_hint,
                "binary_path": version_info.binary_path,
            }
            if version_info.advisory is not None:
                entry["advisory"] = version_info.advisory.to_dict()
            output[tool_name] = entry
        click.echo(json.dumps(output, indent=2))
        return

    console = Console()

    table = Table(title="Tool Versions")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Current", style="yellow")
    table.add_column("Minimum", style="green")
    table.add_column("Status", justify="center")

    if verbose:
        table.add_column("Install Hint", style="dim")

    # Sort tools by name for consistent output
    sorted_tools = sorted(tool_versions.items())
    advisories = []

    for tool_name, version_info in sorted_tools:
        current = version_info.current_version or "unknown"
        minimum = version_info.min_version

        if version_info.version_check_passed and not version_info.below_recommended:
            status = "[green]✓ PASS[/green]"
        elif version_info.version_check_passed and version_info.below_recommended:
            status = "[yellow]⚠ OUTDATED[/yellow]"
        elif version_info.error_message:
            status = "[red]✗ FAIL[/red]"
        else:
            status = "[yellow]? UNKNOWN[/yellow]"

        row = [tool_name, current, minimum, status]

        if verbose:
            hint = version_info.install_hint
            if version_info.advisory and version_info.advisory.update_command:
                hint = version_info.advisory.update_command
            elif version_info.error_message:
                hint = f"{version_info.error_message}. {hint}"
            row.append(hint)

        table.add_row(*row)
        if version_info.advisory is not None:
            advisories.append(version_info.advisory)

    console.print(table)

    if advisories:
        console.print()
        console.print("[bold]Update advisories[/bold]")
        for advisory in advisories:
            line = Text("  ")
            line.append("⚠ ", style="yellow")
            line.append(format_advisory_line(advisory))
            console.print(line)

    # Summary
    total_tools = len(tool_versions)
    passed_tools = sum(1 for v in tool_versions.values() if v.version_check_passed)
    failed_tools = total_tools - passed_tools
    outdated_tools = sum(1 for v in tool_versions.values() if v.below_recommended)

    if failed_tools > 0:
        console.print(
            f"\n[red]⚠️  {failed_tools} tool(s) do not meet "
            f"minimum version requirements.[/red]",
        )
        console.print(
            "[dim]Run with --verbose to see installation instructions.[/dim]",
        )
    elif outdated_tools > 0:
        console.print(
            f"\n[yellow]⚠️  {outdated_tools} tool(s) are below the "
            f"recommended version.[/yellow]",
        )
    else:
        console.print(
            f"\n[green]✅ All {total_tools} tools meet "
            f"minimum version requirements.[/green]",
        )
