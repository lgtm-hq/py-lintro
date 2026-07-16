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

from pathlib import Path

import click
from rich.console import Console

from lintro.cli_utils.onboarding import (
    is_interactive_tty,
    print_install_next_steps,
)
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_lock import (
    InstallLock,
    InstallLockEntry,
    write_install_lock,
)
from lintro.tools.core.tool_installer import ToolInstaller
from lintro.tools.core.tool_registry import ManifestRegistry


@click.command()
@click.argument("tools", nargs=-1)
@click.option(
    "--profile",
    type=str,
    help=(
        "Install tools for a named profile "
        "(minimal, recommended, python, web, ci, full, complete)."
    ),
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
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Non-interactive mode (skip prompts, use defaults).",
)
@click.option(
    "--write-lock",
    is_flag=True,
    help="Write resolved install plan to .lintro-install.lock.json.",
)
def install_command(
    tools: tuple[str, ...],
    profile: str | None,
    *,
    upgrade: bool,
    dry_run: bool,
    install_all: bool,
    yes: bool,
    write_lock: bool,
) -> None:
    """Install or upgrade external tools used by lintro.

    Without arguments, installs all missing tools for the current project.
    Specify tool names to install specific tools, or use --profile for
    predefined sets.

    \f

    Args:
        tools: Tool names to install (positional args).
        profile: Named profile to install.
        upgrade: Upgrade existing tools.
        dry_run: Show plan only.
        install_all: Install all tools.
        yes: Skip interactive prompts.
        write_lock: Write install lock file after planning.

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

    registry = ManifestRegistry.load()
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
        effective_profile = "full"

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

    # Interactive profile/tool selection in TTY mode
    if (
        not tool_list
        and not profile
        and not install_all
        and not yes
        and is_interactive_tty()
        and not dry_run
    ):
        tool_list, effective_profile = _interactive_select(
            console,
            registry,
            effective_profile,
            detected_langs,
        )

    # Create plan
    plan = installer.plan(
        tools=tool_list,
        profile=effective_profile,
        upgrade=upgrade,
        detected_langs=detected_langs,
    )

    # Display plan
    _display_plan(console, plan)

    if write_lock:
        lock_path = Path(".lintro-install.lock.json")
        _write_plan_lock(
            lock_path,
            plan,
            profile=effective_profile,
            detected_langs=detected_langs or [],
        )
        console.print(f"  [green]Wrote install lock:[/green] {lock_path}")

    if not plan.has_work:
        if plan.outdated:
            console.print(
                f"  [yellow]Outdated: {len(plan.outdated)} tool(s) "
                f"(use --upgrade to update)[/yellow]",
            )
        if not plan.outdated and not plan.skipped and not plan.manual:
            console.print("  [green]All tools are already installed.[/green]")
        console.print()
        if plan.skipped or plan.outdated or plan.manual:
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
        if plan.skipped or plan.outdated or plan.manual:
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
    has_issues = failed > 0 or plan.skipped or plan.outdated or plan.manual

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

    print_install_next_steps(console, include_init=True)

    if has_issues:
        raise SystemExit(1)


def _display_plan(console: Console, plan: object) -> None:
    """Render install plan sections."""
    from lintro.tools.core.install_plan import InstallPlan

    assert isinstance(plan, InstallPlan)
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

    if plan.manual:
        console.print(
            f"  [yellow]Manual installation required ({len(plan.manual)}):[/yellow]",
        )
        for tool, hint in plan.manual:
            console.print(f"    {tool.name:<20}")
            for line in hint.split("\n"):
                console.print(f"      [dim]{line}[/dim]")

    if plan.skipped:
        console.print(f"  [yellow]Skipped ({len(plan.skipped)}):[/yellow]")
        for tool, reason in plan.skipped:
            console.print(f"    {tool.name:<20} [dim]{reason}[/dim]")


def _interactive_select(
    console: Console,
    registry: ManifestRegistry,
    profile: str | None,
    detected_langs: list[str] | None,
) -> tuple[list[str] | None, str | None]:
    """Prompt for profile and optionally refine tool list interactively."""
    profiles = registry.profile_names
    default = profile or "recommended"
    if default not in profiles:
        default = "recommended"

    if detected_langs:
        console.print(
            f"  [dim]Detected languages: {', '.join(detected_langs)}[/dim]",
        )

    console.print()
    console.print("  [bold]Select install profile:[/bold]")
    for idx, name in enumerate(profiles, start=1):
        desc = registry.profiles[name].description
        marker = " (default)" if name == default else ""
        console.print(f"    {idx}. {name}{marker} — {desc}")

    choice = click.prompt(
        "Profile number or name",
        default=default,
        show_default=True,
    )
    selected_profile: str | None = None
    if isinstance(choice, int) or (isinstance(choice, str) and choice.isdigit()):
        idx = int(choice) - 1
        if 0 <= idx < len(profiles):
            selected_profile = profiles[idx]
    if selected_profile is None:
        choice_str = str(choice).strip()
        if choice_str in profiles:
            selected_profile = choice_str
        else:
            raise click.UsageError(f"Invalid profile selection: {choice!r}")

    # Resolve profile to tool list and offer per-tool refinement
    resolved = registry.tools_for_profile(
        selected_profile,
        detected_langs,
    )
    if not resolved:
        return None, selected_profile

    console.print()
    console.print(
        f"  [bold]Profile [cyan]{selected_profile}[/cyan] "
        f"resolves to {len(resolved)} tools:[/bold]",
    )

    # Group by category for display
    by_cat: dict[str, list[str]] = {}
    for t in resolved:
        by_cat.setdefault(t.category, []).append(t.name)
    for cat, names in by_cat.items():
        label = {"bundled": "Python", "npm": "npm", "external": "External"}.get(
            cat,
            cat,
        )
        console.print(f"    [dim]{label}:[/dim] {', '.join(sorted(names))}")

    console.print()
    customize = click.prompt(
        "Install all, or customize? [a]ll / [c]ustomize",
        default="a",
        show_default=True,
    )
    if customize.strip().lower() not in ("c", "customize"):
        return None, selected_profile

    # Per-tool toggle
    selected: list[str] = []
    for t in sorted(resolved, key=lambda x: (x.category, x.name)):
        include = click.confirm(f"    Install {t.name}?", default=True)
        if include:
            selected.append(t.name)

    if not selected:
        console.print("  [yellow]No tools selected.[/yellow]")
        raise SystemExit(0)

    return selected, selected_profile


def _write_plan_lock(
    path: Path,
    plan: object,
    *,
    profile: str | None,
    detected_langs: list[str],
) -> None:
    """Serialize install plan to lock file."""
    from lintro.tools.core.install_plan import InstallPlan

    assert isinstance(plan, InstallPlan)
    entries: list[InstallLockEntry] = []
    for tool, cmd in plan.to_install:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                install_hint=cmd,
                status="to_install",
            ),
        )
    for tool, _current, cmd in plan.to_upgrade:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                install_hint=cmd,
                status="to_upgrade",
            ),
        )
    for tool in plan.already_ok:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                status="ok",
            ),
        )
    for tool, installed_ver in plan.outdated:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                install_hint=f"installed: {installed_ver}",
                status="outdated",
            ),
        )
    for tool, hint in plan.manual:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                install_hint=hint,
                status="manual",
            ),
        )
    for tool, reason in plan.skipped:
        entries.append(
            InstallLockEntry(
                name=tool.name,
                version=tool.version,
                install_hint=reason,
                status="skipped",
            ),
        )
    lock = InstallLock(
        profile=profile,
        detected_languages=detected_langs,
        tools=entries,
    )
    write_install_lock(path, lock)


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
