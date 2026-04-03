"""Doctor command for checking tool installation status and version compatibility.

Provides a Flutter doctor-style diagnostic that checks ALL tools, grouped by
install category, with context-aware install hints.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

import click
from rich.console import Console
from rich.text import Text

from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies import get_strategy
from lintro.tools.core.tool_registry import CATEGORY_LABELS, ManifestTool, ToolRegistry
from lintro.tools.core.version_parsing import (
    compare_versions,
    extract_version_from_output,
)
from lintro.utils.environment import (
    EnvironmentReport,
    collect_full_environment,
    render_environment_report,
)


@dataclass
class ToolCheckResult:
    """Result of a tool health check.

    Attributes:
        tool: The manifest tool entry.
        status: ToolStatus value (OK, MISSING, OUTDATED, UNKNOWN).
        installed_version: Detected version string, or None.
        error: Error type if check failed.
        details: Additional error details.
        path: Filesystem path where the tool was found.
        install_hint: Context-aware install command.
        upgrade_hint: Context-aware upgrade command for outdated tools.
    """

    tool: ManifestTool
    status: ToolStatus
    installed_version: str | None = None
    error: str | None = None
    details: str | None = None
    path: str | None = None
    install_hint: str = ""
    upgrade_hint: str = ""


def _check_tool(tool: ManifestTool, context: RuntimeContext) -> ToolCheckResult:
    """Check a single tool's installation status and version.

    Args:
        tool: Manifest tool entry.
        context: Runtime context for install hints.

    Returns:
        ToolCheckResult with status and details.
    """
    strategy = get_strategy(tool.install_type)
    env = context.environment
    if strategy:
        _args = (
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
        )
        hint = strategy.install_hint(*_args)
        upgrade_hint = strategy.upgrade_hint(*_args)
    else:
        hint = f"Install {tool.name} manually"
        upgrade_hint = f"Upgrade {tool.name} manually"

    if not tool.version_command:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="no_command",
            details="No version command defined",
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )

    # Find the main executable (may be a wrapper like "sh", "cargo", etc.)
    main_cmd = tool.version_command[0]
    tool_path = shutil.which(main_cmd)

    if not tool_path:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="not_in_path",
            details=main_cmd,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )

    try:
        result = subprocess.run(
            tool.version_command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            return ToolCheckResult(
                tool=tool,
                status=ToolStatus.MISSING,
                error="command_failed",
                details=f"Exit {result.returncode}: {output[:100]}",
                path=tool_path,
                install_hint=hint,
            )

        version = extract_version_from_output(output, tool.name)
        if not version:
            return ToolCheckResult(
                tool=tool,
                status=ToolStatus.UNKNOWN,
                error="no_version",
                details=f"Output: {output[:100]}",
                path=tool_path,
                install_hint=hint,
            )

        status = _compare_versions(version, tool.version)
        return ToolCheckResult(
            tool=tool,
            status=status,
            installed_version=version,
            path=tool_path,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )
    except subprocess.TimeoutExpired:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="timeout",
            path=tool_path,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )
    except (FileNotFoundError, OSError) as e:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="os_error",
            details=str(e),
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )


def _compare_versions(installed: str, expected: str) -> ToolStatus:
    """Compare installed version against expected minimum.

    Delegates to version_parsing.compare_versions which uses the
    packaging library for robust PEP 440 version comparison.

    Args:
        installed: Installed version string.
        expected: Expected minimum version string.

    Returns:
        ToolStatus.OK, ToolStatus.OUTDATED, or ToolStatus.UNKNOWN.
    """
    try:
        return (
            ToolStatus.OK
            if compare_versions(installed, expected) >= 0
            else ToolStatus.OUTDATED
        )
    except ValueError:
        return ToolStatus.UNKNOWN


def _render_category(
    console: Console,
    category_label: str,
    results: list[ToolCheckResult],
    *,
    verbose: bool = False,
    is_dev: bool = False,
) -> None:
    """Render a category section of the doctor output.

    Args:
        console: Rich console for output.
        category_label: Section header (e.g., "Bundled Python tools").
        results: Check results for this category.
        verbose: Show paths and extra details.
        is_dev: If True, mark missing tools as optional.
    """
    ok_count = sum(1 for r in results if r.status == ToolStatus.OK)
    total = len(results)
    console.print()

    header = Text(f"  {category_label} ", style="bold")
    header.append(f"({ok_count}/{total} OK)", style="dim")
    console.print(header)

    for r in sorted(results, key=lambda x: x.tool.name):
        _render_tool_line(console, r, verbose=verbose, is_dev=is_dev)


def _render_tool_line(
    console: Console,
    r: ToolCheckResult,
    *,
    verbose: bool = False,
    is_dev: bool = False,
) -> None:
    """Render a single tool's status line.

    Args:
        console: Rich console.
        r: Tool check result.
        verbose: Show extra details.
        is_dev: Mark missing as optional instead of error.
    """
    name = f"{r.tool.name:<16}"
    expected = f"(>= {r.tool.version})"

    if r.status == ToolStatus.OK:
        line = Text("    ")
        line.append("[OK] ", style="green bold")
        line.append(name, style="cyan")
        line.append(f"{r.installed_version:<10}", style="yellow")
        line.append(expected, style="dim")
        if verbose and r.path:
            line.append(f"  {r.path}", style="dim")
        console.print(line)

    elif r.status == ToolStatus.OUTDATED:
        line = Text("    ")
        line.append("[!!] ", style="yellow bold")
        line.append(name, style="cyan")
        line.append(f"{r.installed_version:<10}", style="yellow")
        line.append(expected, style="dim")
        console.print(line)
        console.print(f"         [dim]Upgrade: {r.upgrade_hint}[/dim]")

    elif r.status == ToolStatus.MISSING:
        line = Text("    ")
        if is_dev:
            line.append("[--] ", style="dim")
            line.append(name, style="dim")
            line.append("not installed (optional)", style="dim")
        else:
            line.append("[!!] ", style="red bold")
            line.append(name, style="cyan")
            line.append("not installed", style="red")
        console.print(line)
        console.print(f"         [dim]Install: {r.install_hint}[/dim]")

    else:  # unknown
        line = Text("    ")
        line.append("[??] ", style="dim")
        line.append(name, style="cyan")
        line.append("version unknown", style="dim")
        console.print(line)
        if verbose and r.details:
            console.print(f"         [dim]{r.details}[/dim]")


def _generate_markdown_report(
    env: EnvironmentReport,
    context: RuntimeContext,
    results_by_cat: dict[str, list[ToolCheckResult]],
    dev_results: list[ToolCheckResult],
) -> str:
    """Generate a markdown report for GitHub issues.

    Args:
        env: Environment report.
        context: Runtime context.
        results_by_cat: Results grouped by category.
        dev_results: Dev-tier tool results.

    Returns:
        Markdown string.
    """
    lines = ["### Environment", "", "```"]
    lines.append(f"Lintro: {env.lintro.version}")
    lines.append(
        f"Context: {context.install_context} ({context.platform_label})",
    )
    lines.append(f"OS: {env.system.platform_name} ({env.system.architecture})")
    lines.append(f"Python: {env.python.version}")
    if env.node:
        lines.append(f"Node: {env.node.version or 'installed'}")
    if env.rust:
        lines.append(f"Rust: {env.rust.rustc_version or 'installed'}")
    lines.append("```")
    lines.append("")

    lines.append("### Tool Versions")
    lines.append("")
    lines.append("| Category | Tool | Installed | Expected | Status |")
    lines.append("|----------|------|-----------|----------|--------|")

    for cat, results in results_by_cat.items():
        label = CATEGORY_LABELS.get(cat, cat)
        for r in sorted(results, key=lambda x: x.tool.name):
            installed = r.installed_version or "-"
            status_icon = {
                ToolStatus.OK: "OK",
                ToolStatus.MISSING: "MISSING",
                ToolStatus.OUTDATED: "OUTDATED",
                ToolStatus.UNKNOWN: "?",
            }.get(r.status, "?")
            lines.append(
                f"| {label} | {r.tool.name} | {installed} "
                f"| {r.tool.version} | {status_icon} |",
            )
            label = ""

    if dev_results:
        for r in dev_results:
            installed = r.installed_version or "-"
            status = r.status.upper()
            lines.append(
                f"| Dev (optional) | {r.tool.name} | {installed} "
                f"| {r.tool.version} | {status} |",
            )

    lines.append("")
    return "\n".join(lines)


@click.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.option(
    "--tools",
    type=str,
    help="Comma-separated list of tools to check (default: all).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show comprehensive environment information and tool paths.",
)
@click.option(
    "--report",
    is_flag=True,
    help="Generate markdown report for GitHub issues.",
)
@click.option(
    "--fix",
    is_flag=True,
    help="Attempt to install missing tools.",
)
def doctor_command(
    json_output: bool,
    tools: str | None,
    *,
    verbose: bool,
    report: bool,
    fix: bool,
) -> None:
    """Check tool installation status and version compatibility.

    Checks all supported tools grouped by category (bundled, npm, external).
    Shows actionable install commands for missing or outdated tools.

    Args:
        json_output: Output results as JSON.
        tools: Comma-separated tool names to check.
        verbose: Show environment details and tool paths.
        report: Generate markdown report.
        fix: Attempt to install missing tools.

    Raises:
        SystemExit: When missing or broken tools are detected.
        click.UsageError: When --fix is combined with --report or --json.

    Examples:
        lintro doctor
        lintro doctor --tools hadolint,actionlint
        lintro doctor --json
        lintro doctor --verbose
        lintro doctor --fix
    """
    display_console = Console()

    registry = ToolRegistry.load()
    context = RuntimeContext.detect()

    env_report = None
    if verbose or report or json_output:
        env_report = collect_full_environment()

    # Determine which tools to check
    if tools:
        tool_names = [t.strip() for t in tools.split(",") if t.strip()]
        unknown_names = [n for n in tool_names if n not in registry]
        if unknown_names:
            display_console.print(
                f"  [red]Unknown tools: {', '.join(unknown_names)}[/red]",
            )
            available = ", ".join(
                sorted(t.name for t in registry.all_tools(include_dev=True)),
            )
            display_console.print(f"  [dim]Available: {available}[/dim]")
            raise SystemExit(1)
        tools_to_check = [registry.get(n) for n in tool_names]
    else:
        tools_to_check = list(registry.all_tools(include_dev=True))

    # Check all tools
    all_results = [_check_tool(tool, context) for tool in tools_to_check]

    # Split into production and dev
    prod_results = [r for r in all_results if r.tool.tier != "dev"]
    dev_results = [r for r in all_results if r.tool.tier == "dev"]

    # Group production results by category
    results_by_cat: dict[str, list[ToolCheckResult]] = {}
    for r in prod_results:
        results_by_cat.setdefault(r.tool.category, []).append(r)

    # Stats (dev tools don't count as failures)
    ok_count = sum(1 for r in prod_results if r.status == ToolStatus.OK)
    missing_count = sum(1 for r in prod_results if r.status == ToolStatus.MISSING)
    outdated_count = sum(1 for r in prod_results if r.status == ToolStatus.OUTDATED)
    unknown_count = sum(1 for r in prod_results if r.status == ToolStatus.UNKNOWN)
    dev_ok = sum(1 for r in dev_results if r.status == ToolStatus.OK)
    dev_total = len(dev_results)
    total_prod = len(prod_results)

    # ── Reject incompatible flag combinations ──
    if fix and (report or json_output):
        raise click.UsageError("--fix cannot be combined with --report or --json")

    # ── Markdown report mode ──
    if report:
        assert env_report is not None
        markdown = _generate_markdown_report(
            env_report,
            context,
            results_by_cat,
            dev_results,
        )
        click.echo(markdown)
        if missing_count > 0 or outdated_count > 0 or unknown_count > 0:
            sys.exit(1)
        return

    # ── JSON output mode ──
    if json_output:
        _output_json(
            all_results,
            context,
            env_report,
            ok_count,
            missing_count,
            outdated_count,
            unknown_count,
        )
        if missing_count > 0 or outdated_count > 0 or unknown_count > 0:
            sys.exit(1)
        return

    # ── Rich terminal output ──
    display_console.print()
    display_console.print("  [bold]Lintro Doctor[/bold]")
    display_console.print(
        f"  [dim]Context: {context.install_context.value}, "
        f"{context.platform_label}[/dim]",
    )

    if verbose and env_report:
        render_environment_report(display_console, env_report)

    for cat in ("bundled", "npm", "external"):
        if cat in results_by_cat:
            label = CATEGORY_LABELS.get(cat, cat)
            _render_category(
                display_console,
                label,
                results_by_cat[cat],
                verbose=verbose,
            )

    if dev_results:
        _render_category(
            display_console,
            "Dev tools",
            dev_results,
            verbose=verbose,
            is_dev=True,
        )

    # Summary
    display_console.print()
    summary_parts: list[str] = []
    summary_parts.append(f"[green]{ok_count}[/green] OK")
    if missing_count > 0:
        summary_parts.append(f"[red]{missing_count}[/red] missing")
    if outdated_count > 0:
        summary_parts.append(f"[yellow]{outdated_count}[/yellow] outdated")
    if unknown_count > 0:
        summary_parts.append(f"[dim]{unknown_count}[/dim] unknown")
    if dev_total > 0:
        summary_parts.append(f"[dim]{dev_ok}/{dev_total}[/dim] dev")

    display_console.print(
        f"  Summary: {', '.join(summary_parts)} "
        f"[dim]({total_prod} production tools)[/dim]",
    )

    has_fixable = missing_count > 0 or outdated_count > 0
    if has_fixable:
        display_console.print()
        affected_names = [
            r.tool.name
            for r in prod_results
            if r.status in (ToolStatus.MISSING, ToolStatus.OUTDATED)
        ]
        upgrade_flag = " --upgrade" if outdated_count > 0 else ""
        display_console.print(
            f"  [dim]Quick fix: lintro install{upgrade_flag}"
            f" {' '.join(affected_names)}[/dim]",
        )

    display_console.print()

    if fix and has_fixable:
        _run_fix(display_console, prod_results, context, registry)
        # Re-check after fix attempt (unknown may resolve to ok/missing/outdated)
        rechecked = [_check_tool(tool, context) for tool in tools_to_check]
        rechecked_prod = [r for r in rechecked if r.tool.tier != "dev"]
        missing_count = sum(1 for r in rechecked_prod if r.status == ToolStatus.MISSING)
        outdated_count = sum(
            1 for r in rechecked_prod if r.status == ToolStatus.OUTDATED
        )
        unknown_count = sum(1 for r in rechecked_prod if r.status == ToolStatus.UNKNOWN)

    if missing_count > 0 or outdated_count > 0 or unknown_count > 0:
        raise SystemExit(1)


def _output_json(
    all_results: list[ToolCheckResult],
    context: RuntimeContext,
    env_report: EnvironmentReport | None,
    ok_count: int,
    missing_count: int,
    outdated_count: int,
    unknown_count: int,
) -> None:
    """Output doctor results as JSON."""
    tools_json: dict[str, dict[str, str | None]] = {}
    issues: list[dict[str, str]] = []

    for r in all_results:
        tools_json[r.tool.name] = {
            "expected": r.tool.version,
            "installed": r.installed_version,
            "status": r.status,
            "category": r.tool.category,
            "tier": r.tool.tier,
            "install_type": r.tool.install_type,
            "error": r.error,
            "details": r.details,
            "path": r.path,
            "install_hint": r.install_hint,
            "upgrade_hint": r.upgrade_hint,
        }
        if r.status == ToolStatus.MISSING and r.tool.tier != "dev":
            issues.append(
                {
                    "tool": r.tool.name,
                    "severity": "error",
                    "message": f"not installed ({r.error or 'unknown'})",
                    "install_hint": r.install_hint,
                },
            )
        elif r.status == ToolStatus.OUTDATED and r.tool.tier != "dev":
            issues.append(
                {
                    "tool": r.tool.name,
                    "severity": "warning",
                    "message": (f"outdated ({r.installed_version} < {r.tool.version})"),
                    "upgrade_hint": r.upgrade_hint,
                },
            )
        elif r.status == ToolStatus.UNKNOWN and r.tool.tier != "dev":
            issues.append(
                {
                    "tool": r.tool.name,
                    "severity": "warning",
                    "message": f"version unknown ({r.error or 'unparseable output'})",
                    "install_hint": r.install_hint,
                },
            )

    output: dict[str, object] = {
        "context": {
            "install_method": context.install_context.value,
            "platform": context.platform_label,
            "is_ci": context.is_ci,
        },
        "tools": tools_json,
        "issues": issues,
        "summary": {
            "total": ok_count + missing_count + outdated_count + unknown_count,
            "ok": ok_count,
            "missing": missing_count,
            "outdated": outdated_count,
            "unknown": unknown_count,
        },
    }

    if env_report:
        output["environment"] = {
            "lintro": asdict(env_report.lintro),
            "system": asdict(env_report.system),
            "python": asdict(env_report.python),
            "node": asdict(env_report.node) if env_report.node else None,
            "rust": asdict(env_report.rust) if env_report.rust else None,
        }

    click.echo(json.dumps(output, indent=2))


def _run_fix(
    console: Console,
    results: list[ToolCheckResult],
    context: RuntimeContext,
    registry: ToolRegistry,
) -> None:
    """Attempt to install missing/outdated tools via the central installer."""
    from lintro.tools.core.tool_installer import ToolInstaller

    fixable = [
        r for r in results if r.status in (ToolStatus.MISSING, ToolStatus.OUTDATED)
    ]
    if not fixable:
        return

    console.print("  [bold]Attempting to install missing tools...[/bold]")
    console.print()

    installer = ToolInstaller(registry, context)
    tool_names = [r.tool.name for r in fixable]
    has_outdated = any(r.status == ToolStatus.OUTDATED for r in fixable)
    plan = installer.plan(tools=tool_names, upgrade=has_outdated)
    install_results = installer.execute(plan)

    for r in install_results:
        if r.success:
            console.print(
                f"  [green]OK[/green]  {r.tool.name} "
                f"[dim]({r.duration_seconds:.1f}s)[/dim]",
            )
        else:
            console.print(f"  [red]FAIL[/red]  {r.tool.name}: {r.message}")

    if plan.skipped:
        for tool, reason in plan.skipped:
            console.print(f"  [yellow]SKIP[/yellow]  {tool.name}: {reason}")

    console.print()
    console.print("  [dim]Run 'lintro doctor' again to verify.[/dim]")
