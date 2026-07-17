"""Command-line interface for Lintro."""

from __future__ import annotations

import importlib
from typing import Any, cast

import click

from lintro import __version__

# Canonical command name -> "module.path.attr" for lazy loading.
# Aliases point at the same import path as their canonical command.
_LAZY_SUBCOMMANDS: dict[str, str] = {
    "check": "lintro.cli_utils.commands.check.check_command",
    "chk": "lintro.cli_utils.commands.check.check_command",
    "lint": "lintro.cli_utils.commands.check.check_command",
    "config": "lintro.cli_utils.commands.config.config_command",
    "cfg": "lintro.cli_utils.commands.config.config_command",
    "doctor": "lintro.cli_utils.commands.doctor.doctor_command",
    "format": "lintro.cli_utils.commands.format.format_command",
    "fmt": "lintro.cli_utils.commands.format.format_command",
    "fix": "lintro.cli_utils.commands.format.format_command",
    "init": "lintro.cli_utils.commands.init.init_command",
    "install": "lintro.cli_utils.commands.install.install_command",
    "ins": "lintro.cli_utils.commands.install.install_command",
    "licenses": "lintro.cli_utils.commands.licenses.licenses_command",
    "lic": "lintro.cli_utils.commands.licenses.licenses_command",
    "list-tools": "lintro.cli_utils.commands.list_tools.list_tools_command",
    "ls": "lintro.cli_utils.commands.list_tools.list_tools_command",
    "tools": "lintro.cli_utils.commands.list_tools.list_tools_command",
    "review": "lintro.cli_utils.commands.review.review_command",
    "rev": "lintro.cli_utils.commands.review.review_command",
    "setup": "lintro.cli_utils.commands.setup.setup_command",
    "su": "lintro.cli_utils.commands.setup.setup_command",
    "test": "lintro.cli_utils.commands.test.test_command",
    "tst": "lintro.cli_utils.commands.test.test_command",
    "versions": "lintro.cli_utils.commands.versions.versions_command",
    "ver": "lintro.cli_utils.commands.versions.versions_command",
    "version": "lintro.cli_utils.commands.versions.versions_command",
}

# Alias -> canonical name for help rendering.
_CANONICAL_NAMES: dict[str, str] = {
    "check": "check",
    "chk": "check",
    "lint": "check",
    "config": "config",
    "cfg": "config",
    "doctor": "doctor",
    "format": "format",
    "fmt": "format",
    "fix": "format",
    "init": "init",
    "install": "install",
    "ins": "install",
    "licenses": "licenses",
    "lic": "licenses",
    "list-tools": "list-tools",
    "ls": "list-tools",
    "tools": "list-tools",
    "review": "review",
    "rev": "review",
    "setup": "setup",
    "su": "setup",
    "test": "test",
    "tst": "test",
    "versions": "versions",
    "ver": "versions",
    "version": "versions",
}


class LintroGroup(click.Group):
    """Custom Click group with lazy subcommands, aliases, and chaining.

    Subcommands are imported on first use so ``lintro --version`` / ``--help``
    do not pay for check/format/tool_executor/plugin import costs.
    """

    def __init__(
        self,
        *args: Any,
        lazy_subcommands: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the group with optional lazy subcommand map.

        Args:
            *args: Positional args forwarded to ``click.Group``.
            lazy_subcommands: Map of command name -> ``module.attr`` import path.
            **kwargs: Keyword args forwarded to ``click.Group``.
        """
        super().__init__(*args, **kwargs)
        self.lazy_subcommands = lazy_subcommands or {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        """List registered and lazy subcommand names.

        Args:
            ctx: Click context.

        Returns:
            Combined command name list.
        """
        base = list(super().list_commands(ctx))
        lazy = list(self.lazy_subcommands.keys())
        # Preserve insertion order while deduplicating.
        seen: set[str] = set()
        result: list[str] = []
        for name in base + lazy:
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def get_command(
        self,
        ctx: click.Context,
        cmd_name: str,
    ) -> click.Command | None:
        """Resolve a command, importing lazy subcommands on demand.

        Args:
            ctx: Click context.
            cmd_name: Command or alias name.

        Returns:
            The Click command, or None if unknown.
        """
        if cmd_name in self.lazy_subcommands:
            return self._lazy_load(cmd_name)
        return super().get_command(ctx, cmd_name)

    def _lazy_load(self, cmd_name: str) -> click.Command:
        """Import and return a lazily registered subcommand.

        Args:
            cmd_name: Command or alias name present in ``lazy_subcommands``.

        Returns:
            The loaded Click command object.

        Raises:
            ValueError: If the import path does not resolve to a Click command.
        """
        import_path = self.lazy_subcommands[cmd_name]
        modname, attr_name = import_path.rsplit(".", 1)
        # Safe: import paths are a fixed internal whitelist in _LAZY_SUBCOMMANDS.
        module = importlib.import_module(modname)  # nosemgrep: non-literal-import
        cmd_object = getattr(module, attr_name)
        if not isinstance(cmd_object, click.Command):
            msg = (
                f"Lazy loading of {import_path} failed by returning "
                "a non-command object"
            )
            raise ValueError(msg)
        canonical = _CANONICAL_NAMES.get(cmd_name, cmd_name)
        cast(Any, cmd_object)._canonical_name = canonical
        return cmd_object

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
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

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
        from lintro.cli_utils.command_chainer import CommandChainer
        from lintro.tools.core.runtime_discovery import clear_discovery_cache
        from lintro.utils.config import clear_pyproject_cache
        from lintro.utils.logger_setup import setup_cli_logging

        setup_cli_logging()

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
    lazy_subcommands=_LAZY_SUBCOMMANDS,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-v", "--version")
def cli() -> None:
    """Lintro: Unified CLI for code formatting, linting, and quality assurance."""
    pass


def main() -> None:
    """Entry point for the CLI."""
    cli()
