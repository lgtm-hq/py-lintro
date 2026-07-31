"""Pre-execution configuration summary display.

Shows the effective configuration (tools, auto-install, environment)
before tools begin execution, giving users transparency into what
lintro decided before it does anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from lintro.utils.execution.tool_configuration import SkippedTool

#: Fallback ``AI`` row used when no AI status lines were supplied, i.e. when
#: the caller did not inject an AI status renderer. Matches what the AI layer
#: renders for a missing configuration.
AI_STATUS_FALLBACK = "[dim]disabled (no config)[/dim]"


def print_pre_execution_summary(
    *,
    tools_to_run: list[str],
    skipped_tools: list[SkippedTool],
    effective_auto_install: bool,
    is_container: bool,
    is_ci: bool,
    per_tool_auto_install: dict[str, bool | None] | None = None,
    ai_status_lines: list[str] | None = None,
) -> None:
    """Print a pre-execution configuration summary table.

    Args:
        tools_to_run: List of tool names that will execute.
        skipped_tools: List of tools skipped with reasons.
        effective_auto_install: Global effective auto-install setting.
        is_container: Whether running in a container environment.
        is_ci: Whether running in a CI environment.
        per_tool_auto_install: Per-tool auto-install overrides.
        ai_status_lines: Pre-rendered AI status lines from the AI layer
            (``render_ai_status`` on the AI interface facade). When None or
            empty, the AI row reports a missing configuration.
    """
    console = Console()

    table = Table(
        title="Configuration",
        title_style="bold cyan",
        show_header=True,
        header_style="bold",
        border_style="dim",
        padding=(0, 1),
    )

    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value")

    # Environment
    env_parts: list[str] = []
    if is_container:
        env_parts.append("[bold]Container[/bold]")
    if is_ci:
        env_parts.append("CI")
    if not env_parts:
        env_parts.append("Local")
    table.add_row("Environment", ", ".join(env_parts))

    # Auto-install default
    if effective_auto_install:
        auto_source = ""
        if is_container:
            auto_source = " (container default)"
        auto_str = f"[green]enabled{auto_source}[/green]"
    else:
        auto_str = "[dim]disabled[/dim]"
    table.add_row("Auto-install", auto_str)

    # Tools to run
    if tools_to_run:
        tool_lines: list[str] = []
        per_tool = per_tool_auto_install or {}
        for name in tools_to_run:
            override = per_tool.get(name)
            if override is True:
                tool_lines.append(
                    f"  • {name} [green](auto-install: on)[/green]",
                )
            elif override is False:
                tool_lines.append(
                    f"  • {name} [dim](auto-install: off)[/dim]",
                )
            else:
                tool_lines.append(f"  • {name}")
        table.add_row("Tools", "\n".join(tool_lines))
    else:
        table.add_row("Tools", "[dim]None (all tools skipped)[/dim]")

    # Skipped tools
    if skipped_tools:
        skip_lines = [
            f"  • [yellow]{st.name}[/yellow] [dim]({st.reason})[/dim]"
            for st in skipped_tools
        ]
        table.add_row("Skipped", "\n".join(skip_lines))

    # AI configuration (always render for visibility). The lines themselves
    # are rendered by the AI layer so this module stays free of AI imports.
    ai_parts: list[str] = list(ai_status_lines or [AI_STATUS_FALLBACK])

    table.add_row("AI", "\n".join(ai_parts))

    console.print(table)
    console.print()
