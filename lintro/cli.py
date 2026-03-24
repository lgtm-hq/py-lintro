"""Command-line interface for Lintro."""

from typing import Any, cast

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lintro import __version__
from lintro.cli_utils.command_chainer import CommandChainer
from lintro.utils.logger_setup import setup_cli_logging

# Configure loguru for CLI commands (help, version, etc.)
# Only WARNING and above will show. DEBUG logs go to file when tool_executor runs.
setup_cli_logging()

# E402: Module level imports below setup_cli_logging() are intentional.
# Logging must be configured BEFORE importing modules that use loguru,
# otherwise log messages during import get silently dropped or misconfigured.
from lintro.cli_utils.commands.check import check_command  # noqa: E402
from lintro.cli_utils.commands.config import config_command  # noqa: E402
from lintro.cli_utils.commands.doctor import doctor_command  # noqa: E402
from lintro.cli_utils.commands.format import format_command  # noqa: E402
from lintro.cli_utils.commands.init import init_command  # noqa: E402
from lintro.cli_utils.commands.list_tools import list_tools_command  # noqa: E402
from lintro.cli_utils.commands.test import test_command  # noqa: E402
from lintro.cli_utils.commands.versions import versions_command  # noqa: E402
from lintro.tools.core.runtime_discovery import clear_discovery_cache  # noqa: E402
from lintro.utils.config import clear_pyproject_cache  # noqa: E402


class LintroGroup(click.Group):
    """Custom Click group with enhanced help rendering and command chaining.

    This group prints command aliases alongside their canonical names to make
    the CLI help output more discoverable. It also supports command chaining
    with comma-separated commands (e.g., lintro fmt , chk , tst).
    """

    def format_help(
        self,
        ctx: click.Context,
        formatter: click.HelpFormatter,
    ) -> None:
        """Render help with Rich formatting.

        Args:
            ctx: click.Context: The Click context.
            formatter: click.HelpFormatter: The help formatter (unused, we use Rich).
        """
        console = Console()

        # Header panel
        header = Text()
        header.append("🔧 Lintro", style="bold cyan")
        header.append(f" v{__version__}", style="dim")
        console.print(Panel(header, border_style="cyan"))
        console.print()

        # Description
        console.print(
            "[white]Unified CLI for code formatting, linting, "
            "and quality assurance.[/white]",
        )
        console.print()

        # Usage
        console.print("[bold cyan]Usage:[/bold cyan]")
        console.print("  lintro [OPTIONS] COMMAND [ARGS]...")
        console.print("  lintro COMMAND1 , COMMAND2 , ...  [dim](chain commands)[/dim]")
        console.print()

        # Commands table
        commands = self.list_commands(ctx)
        canonical_map: dict[str, tuple[click.Command, list[str]]] = {}
        for name in commands:
            cmd = self.get_command(ctx, name)
            if cmd is None:
                continue
            cmd_any = cast(Any, cmd)
            if not hasattr(cmd_any, "_canonical_name"):
                cmd_any._canonical_name = name
            canonical = cast(str, getattr(cmd_any, "_canonical_name", name))
            if canonical not in canonical_map:
                canonical_map[canonical] = (cmd, [])
            if name != canonical:
                canonical_map[canonical][1].append(name)

        table = Table(title="Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="cyan", no_wrap=True)
        table.add_column("Alias", style="yellow", no_wrap=True)
        table.add_column("Description", style="white")

        for canonical, (cmd, aliases) in sorted(canonical_map.items()):
            alias_str = ", ".join(aliases) if aliases else "-"
            table.add_row(canonical, alias_str, cmd.get_short_help_str())

        console.print(table)
        console.print()

        # Options
        console.print("[bold cyan]Options:[/bold cyan]")
        console.print("  [yellow]-v, --version[/yellow]  Show the version and exit.")
        console.print("  [yellow]-h, --help[/yellow]     Show this message and exit.")
        console.print()

        # Examples
        console.print("[bold cyan]Examples:[/bold cyan]")
        console.print("  [dim]# Check all files[/dim]")
        console.print("  lintro check .")
        console.print()
        console.print("  [dim]# Format and then check[/dim]")
        console.print("  lintro fmt . , chk .")
        console.print()
        console.print("  [dim]# Show tool versions[/dim]")
        console.print("  lintro versions")

    def format_commands(
        self,
        ctx: click.Context,
        formatter: click.HelpFormatter,
    ) -> None:
        """Render command list with aliases in the help output.

        Args:
            ctx: click.Context: The Click context.
            formatter: click.HelpFormatter: The help formatter to write to.
        """
        # This is now handled by format_help, but keep for compatibility
        pass

    def invoke(
        self,
        ctx: click.Context,
    ) -> int:
        """Handle command execution with support for command chaining.

        Supports chaining commands with commas, e.g.: lintro fmt , chk , tst

        Args:
            ctx: click.Context: The Click context.

        Returns:
            int: Exit code from command execution.

        Raises:
            SystemExit: If a command exits with a non-zero exit code.
        """
        # Clear caches at start of each invocation to ensure fresh tool
        # detection and pyproject.toml loading across working directories
        clear_discovery_cache()
        clear_pyproject_cache()

        all_args = ctx.protected_args + ctx.args

        if all_args:
            chainer = CommandChainer(self)

            if chainer.should_chain(all_args):
                # Normalize arguments and group into command chains
                normalized = chainer.normalize_args(all_args)
                groups = chainer.group_commands(normalized)

                # Execute command chain
                final_exit_code = chainer.execute_chain(ctx, groups)
                if final_exit_code != 0:
                    raise SystemExit(final_exit_code)
                return 0

        # Normal single command execution
        result = super().invoke(ctx)
        return int(result) if isinstance(result, int) else 0


@click.group(
    cls=LintroGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-v", "--version")
def cli() -> None:
    """Lintro: Unified CLI for code formatting, linting, and quality assurance."""
    pass


# Register canonical commands and set _canonical_name for help
cast(Any, check_command)._canonical_name = "check"
cast(Any, config_command)._canonical_name = "config"
cast(Any, doctor_command)._canonical_name = "doctor"
cast(Any, format_command)._canonical_name = "format"
cast(Any, init_command)._canonical_name = "init"
cast(Any, test_command)._canonical_name = "test"
cast(Any, list_tools_command)._canonical_name = "list-tools"
cast(Any, versions_command)._canonical_name = "versions"

cli.add_command(check_command, name="check")
cli.add_command(config_command, name="config")
cli.add_command(doctor_command, name="doctor")
cli.add_command(format_command, name="format")
cli.add_command(init_command, name="init")
cli.add_command(test_command, name="test")
cli.add_command(list_tools_command, name="list-tools")
cli.add_command(versions_command, name="versions")

# Register aliases
cli.add_command(check_command, name="chk")
cli.add_command(check_command, name="lint")
cli.add_command(config_command, name="cfg")
cli.add_command(format_command, name="fmt")
cli.add_command(format_command, name="fix")
cli.add_command(test_command, name="tst")
cli.add_command(list_tools_command, name="ls")
cli.add_command(list_tools_command, name="tools")
cli.add_command(versions_command, name="ver")
cli.add_command(versions_command, name="version")


def main() -> None:
    """Entry point for the CLI."""
    cli()
