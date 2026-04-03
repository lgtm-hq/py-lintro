"""Install command for managing lintro tool dependencies.

Installs, upgrades, and manages the external tools that lintro uses for
linting, formatting, and code quality checks.

Usage:
    lintro install                    # Install all missing tools
    lintro install ruff prettier      # Install specific tools
    lintro install --profile minimal  # Install a profile's tools
    lintro install --upgrade          # Upgrade to manifest versions
    lintro install --dry-run          # Show plan without executing
"""

from __future__ import annotations

import click
from rich.console import Console

from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.tool_installer import ToolInstaller
from lintro.tools.core.tool_registry import ToolRegistry


@click.command()
@click.argument("tools", nargs=-1)
@click.option(
    "--profile",
    type=str,
    help="Install tools for a named profile (minimal, recommended, complete, ci).",
)
@click.option(
    "--upgrade",
    is_flag=True,
    help="Upgrade already-installed tools to manifest versions.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be installed without executing.",
)
@click.option(
    "--all",
    "install_all",
    is_flag=True,
    help="Install all supported tools.",
)
def install_command(
    tools: tuple[str, ...],
    profile: str | None,
    *,
    upgrade: bool,
    dry_run: bool,
    install_all: bool,
) -> None:
    """Install or upgrade external tools used by lintro.

    Without arguments, installs all missing tools for the current project.
    Specify tool names to install specific tools, or use --profile for
    predefined sets.

    Args:
        tools: Tool names to install (positional args).
        profile: Named profile to install.
        upgrade: Upgrade existing tools.
        dry_run: Show plan only.
        install_all: Install all tools.

    Raises:
        SystemExit: When tool installation fails.
        click.UsageError: When conflicting options or invalid profile given.

    Examples:
        lintro install
        lintro install ruff prettier hadolint
        lintro install --profile minimal
        lintro install --upgrade
        lintro install --dry-run
    """
    console = Console()

    registry = ToolRegistry.load()
    context = RuntimeContext.detect()
    installer = ToolInstaller(registry, context)

    # Determine tool list — reject conflicting selectors
    tool_list: list[str] | None = list(tools) if tools else None
    effective_profile = profile
    selectors = sum(bool(x) for x in (tool_list, install_all, profile))
    if selectors > 1:
        raise click.UsageError(
            "Cannot combine tool names, --profile, and --all; supply exactly one",
        )
    if install_all:
        effective_profile = "complete"

    # Validate tool names against registry
    if tool_list:
        unknown = [n for n in tool_list if n not in registry]
        if unknown:
            available = ", ".join(
                sorted(t.name for t in registry.all_tools(include_dev=True)),
            )
            raise click.UsageError(
                f"Unknown tools: {', '.join(unknown)}. " f"Available: {available}",
            )

    # Validate profile name
    if effective_profile and effective_profile not in registry.profile_names:
        raise click.UsageError(
            f"Unknown profile {effective_profile!r}. "
            f"Available: {', '.join(registry.profile_names)}",
        )

    # Detect project languages for auto-detect / recommended profile
    detected_langs: list[str] | None = None
    if not tool_list:
        if not effective_profile:
            effective_profile = "recommended"
        if effective_profile == "recommended":
            detected_langs = _detect_languages()

    # Create plan
    plan = installer.plan(
        tools=tool_list,
        profile=effective_profile,
        upgrade=upgrade,
        detected_langs=detected_langs,
    )

    # Display plan
    console.print()
    if plan.to_install:
        console.print(f"  [bold]To install ({len(plan.to_install)}):[/bold]")
        for tool, cmd in plan.to_install:
            console.print(f"    {tool.name:<20} [dim]{cmd}[/dim]")

    if plan.to_upgrade:
        console.print(f"  [bold]To upgrade ({len(plan.to_upgrade)}):[/bold]")
        for tool, current, cmd in plan.to_upgrade:
            console.print(
                f"    {tool.name:<20} [yellow]{current}[/yellow] → "
                f"[green]{tool.version}[/green]  [dim]{cmd}[/dim]",
            )

    if plan.already_ok:
        console.print(
            f"  [dim]Already installed: {len(plan.already_ok)} tools[/dim]",
        )

    if plan.skipped:
        console.print(f"  [yellow]Skipped ({len(plan.skipped)}):[/yellow]")
        for tool, reason in plan.skipped:
            console.print(f"    {tool.name:<20} [dim]{reason}[/dim]")

    if not plan.has_work:
        if plan.outdated:
            console.print(
                f"  [yellow]Outdated: {len(plan.outdated)} tool(s) "
                f"(use --upgrade to update)[/yellow]",
            )
        if not plan.outdated and not plan.skipped:
            console.print("  [green]All tools are already installed.[/green]")
        console.print()
        if plan.skipped or plan.outdated:
            raise SystemExit(1)
        return

    if dry_run:
        console.print()
        console.print(
            f"  [dim]Dry run: {plan.total_actions} tool(s) would be installed.[/dim]",
        )
        if plan.outdated:
            console.print(
                f"  [yellow]Outdated: {len(plan.outdated)} tool(s) "
                f"(use --upgrade to update)[/yellow]",
            )
        console.print()
        if plan.skipped or plan.outdated:
            raise SystemExit(1)
        return

    # Execute
    console.print()
    results = installer.execute(plan)

    # Report results
    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)

    for r in results:
        if r.success:
            console.print(
                f"  [green]OK[/green]  {r.tool.name} "
                f"[dim]({r.duration_seconds:.1f}s)[/dim]",
            )
        else:
            console.print(f"  [red]FAIL[/red]  {r.tool.name}: {r.message}")

    console.print()
    has_issues = failed > 0 or plan.skipped or plan.outdated

    if failed > 0:
        console.print(
            f"  [yellow]{succeeded} installed, {failed} failed[/yellow]",
        )
    elif has_issues:
        console.print(f"  [green]{succeeded} tools installed.[/green]")
    else:
        console.print(f"  [green]All {succeeded} tools installed.[/green]")

    if plan.outdated:
        console.print(
            f"  [yellow]Outdated: {len(plan.outdated)} tool(s) "
            f"(use --upgrade to update)[/yellow]",
        )

    console.print()
    if has_issues:
        raise SystemExit(1)


def _detect_languages() -> list[str]:
    """Detect project languages for profile resolution.

    Uses the full-ecosystem detector to cover Docker, YAML, Markdown,
    TOML, Shell, SQL, GitHub Actions, Astro, Svelte, Vue, etc.

    Returns:
        List of detected language identifiers.
    """
    try:
        from lintro.utils.project_detection import detect_project_languages
    except ImportError:
        return []
    try:
        return detect_project_languages()
    except OSError:
        return []
