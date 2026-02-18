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
    from lintro.ai.config import AIConfig
    from lintro.utils.execution.tool_configuration import SkippedTool


def print_pre_execution_summary(
    *,
    tools_to_run: list[str],
    skipped_tools: list[SkippedTool],
    effective_auto_install: bool,
    is_container: bool,
    is_ci: bool,
    per_tool_auto_install: dict[str, bool | None] | None = None,
    ai_config: AIConfig | None = None,
) -> None:
    """Print a pre-execution configuration summary table.

    Args:
        tools_to_run: List of tool names that will execute.
        skipped_tools: List of tools skipped with reasons.
        effective_auto_install: Global effective auto-install setting.
        is_container: Whether running in a container environment.
        is_ci: Whether running in a CI environment.
        per_tool_auto_install: Per-tool auto-install overrides.
        ai_config: AI configuration, if available.
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

    # AI configuration
    if ai_config is not None and ai_config.enabled:
        import os

        from lintro.ai.availability import is_provider_available
        from lintro.ai.providers import (
            DEFAULT_API_KEY_ENVS,
            DEFAULT_MODELS,
            get_default_model,
        )

        ai_parts: list[str] = []
        provider_name = ai_config.provider.lower()
        supported = set(DEFAULT_MODELS.keys())

        # Check: unknown provider
        if provider_name not in supported:
            ai_parts.append("[red]enabled (unknown provider)[/red]")
            names = ", ".join(sorted(supported))
            ai_parts.append(
                f"  [yellow]'{ai_config.provider}' is not supported. "
                f"Use: {names}[/yellow]",
            )
        else:
            # Check SDK availability
            sdk_ok = is_provider_available(provider_name)

            # Check API key
            key_env = ai_config.api_key_env or DEFAULT_API_KEY_ENVS.get(
                provider_name,
                "",
            )
            key_set = bool(os.environ.get(key_env)) if key_env else False

            if sdk_ok and key_set:
                ai_parts.append("[green]enabled[/green]")
            elif not sdk_ok:
                ai_parts.append(
                    "[red]enabled (SDK not installed)[/red]",
                )
                ai_parts.append(
                    "  [yellow]run: uv pip install" " 'lintro\\[ai]'[/yellow]",
                )
            elif not key_set:
                ai_parts.append(
                    "[yellow]enabled (API key missing)[/yellow]",
                )
                ai_parts.append(
                    f"  [yellow]set {key_env} env var[/yellow]",
                )

        ai_parts.append(f"  provider: {ai_config.provider}")

        effective_model = ai_config.model or get_default_model(
            provider_name,
        )
        if effective_model:
            model_label = effective_model
            if not ai_config.model:
                model_label += " [dim](default)[/dim]"
            ai_parts.append(f"  model: {model_label}")

        # auto_apply warning
        if ai_config.auto_apply:
            if is_ci:
                ai_parts.append("  auto-apply: [green]on[/green]")
            else:
                ai_parts.append(
                    "  auto-apply: [bold red]on (files will be "
                    "modified without confirmation)[/bold red]",
                )

        # Parallel workers
        ai_parts.append(
            f"  parallel: {ai_config.max_parallel_calls} workers",
        )

        table.add_row("AI", "\n".join(ai_parts))

    console.print(table)
    console.print()
