"""Doctor command for checking external tool installation status.

This command checks tools that users must install separately (not bundled with lintro).
Bundled Python tools (ruff, black, bandit, mypy, yamllint) are installed
as dependencies and managed via pyproject.toml - use `pip check` or `uv sync` for those.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import asdict, dataclass

import click
from rich.console import Console
from rich.table import Table

from lintro._tool_versions import get_all_expected_versions
from lintro.tools.core.version_parsing import extract_version_from_output
from lintro.utils.environment import (
    EnvironmentReport,
    collect_full_environment,
    render_environment_report,
)


def _pytest_version_command() -> list[str]:
    """Build the pytest version check command.

    Uses the same venv-first resolution as PytestBuilder.get_command():
    checks sysconfig scripts dir first, then PATH, then python -m fallback.

    Returns:
        Command list to check pytest version.
    """
    # In a venv, check the venv scripts dir first (matches PytestBuilder logic)
    if sys.prefix != sys.base_prefix:
        scripts_dir = sysconfig.get_path("scripts")
        venv_pytest = shutil.which("pytest", path=scripts_dir) if scripts_dir else None
        if venv_pytest:
            return [venv_pytest, "--version"]
    # PATH discovery (Homebrew, system, pipx, etc.)
    pytest_path = shutil.which("pytest")
    if pytest_path:
        return [pytest_path, "--version"]
    return [sys.executable, "-m", "pytest", "--version"]


@dataclass
class VersionCheckResult:
    """Result of a version check for a tool.

    Attributes:
        version: The parsed version string if successful.
        error: Error type if version check failed (not_in_path, command_failed,
               no_version, timeout, os_error, no_command).
        details: Additional details about the error (raw output or error message).
        path: The filesystem path where the tool is installed.
    """

    version: str | None = None
    error: str | None = None
    details: str | None = None
    path: str | None = None


# Map tool names to commands (external tools only)
TOOL_COMMANDS: dict[str, list[str]] = {
    "actionlint": ["actionlint", "--version"],
    "cargo_audit": ["cargo", "audit", "--version"],
    "clippy": ["cargo", "clippy", "--version"],
    "gitleaks": ["gitleaks", "version"],
    "hadolint": ["hadolint", "--version"],
    "markdownlint": ["markdownlint-cli2", "--version"],
    "osv_scanner": ["osv-scanner", "--version"],
    "oxfmt": ["oxfmt", "--version"],
    "oxlint": ["oxlint", "--version"],
    "pytest": _pytest_version_command(),
    "rustfmt": ["rustfmt", "--version"],
    "semgrep": ["semgrep", "--version"],
    "shellcheck": ["shellcheck", "--version"],
    "shfmt": ["shfmt", "--version"],
    "sqlfluff": ["sqlfluff", "--version"],
    "taplo": ["taplo", "--version"],
}


def _check_tool_commands_coverage() -> list[str]:
    """Check for expected tools that don't have version commands defined.

    Uses get_all_expected_versions() to match the same tool set used by
    the doctor checklist.

    Returns:
        List of tool names that are expected but not in TOOL_COMMANDS.
    """
    return [tool for tool in get_all_expected_versions() if tool not in TOOL_COMMANDS]


def _get_installed_version(tool_name: str) -> VersionCheckResult:
    """Get the installed version of an external tool.

    Args:
        tool_name: Name of the tool to check.

    Returns:
        VersionCheckResult with version if successful, or error details if not.
    """
    command = TOOL_COMMANDS.get(tool_name)
    if not command:
        return VersionCheckResult(error="no_command")

    # Check if the main executable exists and capture its path
    main_cmd = command[0]
    tool_path = shutil.which(main_cmd)
    if not tool_path:
        return VersionCheckResult(error="not_in_path", details=main_cmd)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            return VersionCheckResult(
                error="command_failed",
                details=f"Exit {result.returncode}: {output[:100]}",
                path=tool_path,
            )

        version = extract_version_from_output(output, tool_name)
        if not version:
            return VersionCheckResult(
                error="no_version",
                details=f"Output: {output[:100]}",
                path=tool_path,
            )

        return VersionCheckResult(version=version, path=tool_path)
    except subprocess.TimeoutExpired:
        return VersionCheckResult(error="timeout", path=tool_path)
    except (FileNotFoundError, OSError) as e:
        return VersionCheckResult(error="os_error", details=str(e))


def _compare_versions(installed: str, expected: str) -> str:
    """Compare installed version against expected.

    Args:
        installed: Installed version string.
        expected: Expected minimum version string.

    Returns:
        str: Status string - "ok", "outdated", or "unknown".
    """
    try:
        installed_parts = [int(x) for x in installed.split(".")[:3]]
        expected_parts = [int(x) for x in expected.split(".")[:3]]

        # Pad to equal length
        while len(installed_parts) < 3:
            installed_parts.append(0)
        while len(expected_parts) < 3:
            expected_parts.append(0)

        if installed_parts >= expected_parts:
            return "ok"
        return "outdated"
    except (ValueError, AttributeError):
        # Can't compare versions - treat as unknown rather than silently passing
        return "unknown"


def _format_failure_reason(
    error: str | None,
    details: str | None,
    *,
    verbose: bool = False,
) -> str:
    """Format a failure reason for display.

    Args:
        error: Error type from VersionCheckResult.
        details: Additional details from VersionCheckResult.
        verbose: If True, include full details.

    Returns:
        Formatted reason string for display.
    """
    if not error:
        return ""

    reason_map = {
        "not_in_path": "Not in PATH",
        "command_failed": "Cmd failed",
        "no_version": "No version parsed",
        "timeout": "Timeout",
        "os_error": "OS error",
        "no_command": "No cmd defined",
    }
    reason = reason_map.get(error, error)

    if verbose and details:
        return f"{reason}: {details}"
    return reason


def _generate_markdown_report(
    env: EnvironmentReport,
    tool_results: dict[str, dict[str, str | None]],
) -> str:
    """Generate markdown report suitable for GitHub issues.

    Args:
        env: Environment report data.
        tool_results: Tool check results.

    Returns:
        Markdown-formatted report string.
    """
    lines = ["### Environment", "", "```"]
    lines.append(f"Lintro: {env.lintro.version}")
    lines.append(f"OS: {env.system.platform_name} ({env.system.architecture})")
    lines.append(f"Python: {env.python.version}")
    if env.node:
        lines.append(f"Node: {env.node.version or 'installed'}")
    if env.rust:
        lines.append(f"Rust: {env.rust.rustc_version or 'installed'}")
    if env.ci:
        lines.append(f"CI: {env.ci.name}")
    lines.append("```")
    lines.append("")

    # Tool versions table
    lines.append("### Tool Versions")
    lines.append("")
    lines.append("| Tool | Version | Status |")
    lines.append("|------|---------|--------|")

    for tool_name, info in sorted(tool_results.items()):
        version = info.get("installed") or "-"
        status = info.get("status") or "unknown"
        status_icon = {"ok": "✓", "missing": "✗", "outdated": "⚠", "unknown": "?"}.get(
            status,
            "?",
        )
        lines.append(f"| {tool_name} | {version} | {status_icon} {status} |")

    lines.append("")

    # Config info
    if env.lintro.config_file:
        lines.append("### Config")
        lines.append("")
        lines.append(f"Config file: `{env.lintro.config_file}`")
        lines.append("")

    return "\n".join(lines)


@click.command()
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output as JSON.",
)
@click.option(
    "--tools",
    type=str,
    help="Comma-separated list of tools to check (default: all).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show comprehensive environment information.",
)
@click.option(
    "--report",
    is_flag=True,
    help="Generate markdown report for GitHub issues.",
)
def doctor_command(
    json_output: bool,
    tools: str | None,
    *,
    verbose: bool,
    report: bool,
) -> None:
    """Check external tool installation status and version compatibility.

    Checks tools that must be installed separately (hadolint, actionlint,
    etc.). Bundled Python tools are managed via pip/uv.

    Args:
        json_output: If True, output results as JSON.
        tools: Comma-separated list of tools to check, or None for all.
        verbose: If True, show comprehensive environment information.
        report: If True, generate markdown report for GitHub issues.

    Raises:
        SystemExit: If there are missing or outdated tools.

    Examples:
        lintro doctor
        lintro doctor --tools hadolint,actionlint
        lintro doctor --json
        lintro doctor --verbose
        lintro doctor --report
    """
    console = Console(stderr=True)
    display_console = Console()

    # Collect environment info (needed for verbose, report, and json modes)
    env_report = None
    if verbose or report or json_output:
        env_report = collect_full_environment()

    # Warn about tools without command definitions
    uncovered_tools = _check_tool_commands_coverage()
    if uncovered_tools and not json_output and not report:
        console.print(
            f"[yellow]Warning: No version command defined for: "
            f"{', '.join(uncovered_tools)}[/yellow]",
        )

    # Use all expected versions (manifest + npm + TOOL_VERSIONS)
    all_versions = get_all_expected_versions()
    if tools:
        tool_list = [t.strip() for t in tools.split(",")]
        versions_to_check = {k: v for k, v in all_versions.items() if k in tool_list}
    else:
        versions_to_check = all_versions

    results: dict[str, dict[str, str | None]] = {}
    ok_count = 0
    missing_count = 0
    outdated_count = 0
    unknown_count = 0

    for tool_name, expected_version in sorted(versions_to_check.items()):
        check_result = _get_installed_version(tool_name)

        if check_result.version is None:
            status = "missing"
            missing_count += 1
        else:
            status = _compare_versions(check_result.version, expected_version)
            if status == "ok":
                ok_count += 1
            elif status == "outdated":
                outdated_count += 1
            else:  # unknown
                unknown_count += 1

        results[tool_name] = {
            "expected": expected_version,
            "installed": check_result.version,
            "status": status,
            "error": check_result.error,
            "details": check_result.details,
            "path": check_result.path,
        }

    # Markdown report mode
    if report:
        # env_report is guaranteed to be set since report=True implies
        # the condition (verbose or report or json_output) was True above
        assert env_report is not None
        markdown = _generate_markdown_report(env_report, results)
        click.echo(markdown)
        return

    # JSON output mode
    if json_output:
        # Build issues array for easier parsing
        issues: list[dict[str, str]] = []
        for tool_name, info in results.items():
            if info["status"] == "missing":
                error_detail = info.get("error") or "unknown reason"
                issues.append(
                    {
                        "tool": tool_name,
                        "severity": "error",
                        "message": f"not installed ({error_detail})",
                    },
                )
            elif info["status"] == "outdated":
                installed = info["installed"]
                expected = info["expected"]
                issues.append(
                    {
                        "tool": tool_name,
                        "severity": "warning",
                        "message": f"outdated ({installed} < {expected})",
                    },
                )

        output: dict[str, object] = {
            "tools": results,
            "issues": issues,
            "summary": {
                "total": len(results),
                "ok": ok_count,
                "missing": missing_count,
                "outdated": outdated_count,
                "unknown": unknown_count,
            },
        }
        if uncovered_tools:
            output["warnings"] = {
                "uncovered_tools": uncovered_tools,
            }
        # Include environment info in JSON output
        if env_report:
            output["environment"] = {
                "lintro": asdict(env_report.lintro),
                "system": asdict(env_report.system),
                "python": asdict(env_report.python),
                "node": asdict(env_report.node) if env_report.node else None,
                "rust": asdict(env_report.rust) if env_report.rust else None,
                "go": asdict(env_report.go) if env_report.go else None,
                "ruby": asdict(env_report.ruby) if env_report.ruby else None,
                "ci": asdict(env_report.ci) if env_report.ci else None,
                "project": asdict(env_report.project) if env_report.project else None,
            }
        click.echo(json.dumps(output, indent=2))
        # Exit non-zero if any tools are missing or outdated
        if missing_count > 0 or outdated_count > 0:
            sys.exit(1)
        return

    # Verbose mode: show environment report first
    if verbose and env_report:
        render_environment_report(display_console, env_report)

    # Rich table output
    table = Table(title="Tool Health Check")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Expected", style="dim")
    table.add_column("Installed", style="yellow")
    table.add_column("Status", justify="center")
    table.add_column("Reason", style="dim")
    if verbose:
        table.add_column("Path", style="dim", no_wrap=True)

    for tool_name, info in results.items():
        expected = info["expected"] or "-"
        installed = info["installed"] or "-"

        if info["status"] == "ok":
            status = "[green]✓ OK[/green]"
        elif info["status"] == "missing":
            status = "[red]✗ Missing[/red]"
        elif info["status"] == "outdated":
            status = "[yellow]⚠ Outdated[/yellow]"
        else:  # unknown
            status = "[dim]? Unknown[/dim]"

        # Show reason for missing or failed tools
        reason = ""
        if info["status"] == "missing":
            reason = _format_failure_reason(
                info.get("error"),
                info.get("details"),
                verbose=verbose,
            )

        path_display = info.get("path") or "-"
        if verbose:
            table.add_row(tool_name, expected, installed, status, reason, path_display)
        else:
            table.add_row(tool_name, expected, installed, status, reason)

    display_console.print(table)
    display_console.print()

    # Summary
    total = len(results)
    if missing_count == 0 and outdated_count == 0:
        if unknown_count > 0:
            display_console.print(
                f"[green]✅ {ok_count} tool(s) OK[/green], "
                f"[dim]{unknown_count} with unknown version format[/dim]",
            )
        else:
            display_console.print(
                f"[green]✅ All {total} tools are properly installed.[/green]",
            )
    else:
        if missing_count > 0:
            display_console.print(f"[red]✗ {missing_count} tool(s) missing[/red]")
        if outdated_count > 0:
            display_console.print(
                f"[yellow]⚠ {outdated_count} tool(s) outdated[/yellow]",
            )
        if unknown_count > 0:
            display_console.print(
                f"[dim]? {unknown_count} tool(s) with unknown version format[/dim]",
            )
        display_console.print()
        display_console.print(
            "[dim]Run 'lintro versions --verbose' for installation instructions.[/dim]",
        )

    # Exit with error if any tools are missing or outdated
    if missing_count > 0 or outdated_count > 0:
        raise SystemExit(1)
