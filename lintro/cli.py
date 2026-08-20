"""Command-line interface for Lintro."""

from __future__ import annotations

import codecs
import contextlib
import importlib
import sys
from typing import Any, TextIO, cast

import click

from lintro import __version__


def _is_utf8_encoding(encoding: str | None) -> bool:
    """Return whether *encoding* names UTF-8.

    Args:
        encoding: Encoding name from a text stream, or ``None``.

    Returns:
        ``True`` when *encoding* is UTF-8 (any common spelling).
    """
    if encoding is None:
        return False
    try:
        return codecs.lookup(encoding).name == "utf-8"
    except LookupError:
        # Unknown codec name — cannot be a UTF-8 alias.
        return False


def _reconfigure_stream_utf8(stream: TextIO) -> None:
    """Reconfigure *stream* to UTF-8 when it is not already UTF-8.

    Args:
        stream: Stdout/stderr (or compatible) text stream.
    """
    if _is_utf8_encoding(getattr(stream, "encoding", None)):
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    # Closed streams or non-reconfigurable wrappers — leave as-is.
    with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
        reconfigure(encoding="utf-8", errors="replace")


def ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr for ASCII (and other non-UTF-8) locales.

    Rich help output includes emoji (e.g. the wrench in the banner). Under
    ASCII locales such as ``en_US.US-ASCII``, writing that output raises
    ``UnicodeEncodeError``. CPython's UTF-8 mode rescues ``C``/``POSIX`` but
    not other ASCII locales, and ``PYTHONUTF8``/``PYTHONIOENCODING`` do not
    reach Nuitka onefile binaries — only an in-process reconfigure does.
    """
    _reconfigure_stream_utf8(sys.stdout)
    _reconfigure_stream_utf8(sys.stderr)


# Canonical command name -> "module.path.attr" for lazy loading.
# Aliases point at the same import path as their canonical command.
_LAZY_SUBCOMMANDS: dict[str, str] = {
    "badge": "lintro.cli_utils.commands.badge.badge_command",
    "check": "lintro.cli_utils.commands.check.check_command",
    "chk": "lintro.cli_utils.commands.check.check_command",
    "lint": "lintro.cli_utils.commands.check.check_command",
    "completions": "lintro.cli_utils.commands.completions.completions_command",
    "comp": "lintro.cli_utils.commands.completions.completions_command",
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
    "mcp": "lintro.cli_utils.commands.mcp.mcp_command",
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
    "badge": "badge",
    "check": "check",
    "chk": "check",
    "lint": "check",
    "completions": "completions",
    "comp": "completions",
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
    "mcp": "mcp",
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

# Longest short-help string rendered without truncation. Well above every entry
# in ``_COMMAND_SHORT_HELP``, so the sync test compares full sentences rather
# than Click's ellipsis-truncated form.
SHORT_HELP_LIMIT: int = 200

# Canonical command -> short help for ``--help`` without importing subcommands.
#
# Each value must equal ``cmd.get_short_help_str(limit=SHORT_HELP_LIMIT)`` for
# the command it names; ``tests/unit/cli/test_lazy_subcommands.py`` fails the
# build when a subcommand's docstring and this table drift apart.
_COMMAND_SHORT_HELP: dict[str, str] = {
    "badge": "Generate a shields.io markdown badge for the project health score.",
    "check": "Check files for issues using the specified tools.",
    "completions": "Print a shell completion script for bash, zsh, or fish.",
    "config": "Display Lintro configuration status.",
    "doctor": "Check tool installation status and version compatibility.",
    "format": "Format code using configured formatting tools.",
    "init": "Initialize Lintro configuration for your project.",
    "install": "Install or upgrade external tools used by lintro.",
    "licenses": "Check dependency licenses for policy compliance.",
    "list-tools": "List all available tools and their configurations.",
    "mcp": "Start the lintro MCP server on stdio.",
    "review": "Run AI-powered diff-based code review, plus advisory AI finders.",
    "setup": "Set up lintro for your project.",
    "test": "Run tests using pytest.",
    "versions": "Display version information for all supported tools.",
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
        # Cache under the requested name so aliases do not re-import/mutate.
        existing = self.commands.get(cmd_name)
        if existing is not None:
            return existing

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
        self.add_command(cmd_object, name=cmd_name)
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

        # Commands table from static metadata — do not import subcommands.
        commands = self.list_commands(ctx)
        canonical_map: dict[str, list[str]] = {}
        for name in commands:
            canonical = _CANONICAL_NAMES.get(name, name)
            if canonical not in canonical_map:
                canonical_map[canonical] = []
            if name != canonical:
                canonical_map[canonical].append(name)

        table = Table(title="Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="cyan", no_wrap=True)
        table.add_column("Alias", style="yellow", no_wrap=True)
        table.add_column("Description", style="white")

        for canonical, aliases in sorted(canonical_map.items()):
            alias_str = ", ".join(aliases) if aliases else "-"
            description = _COMMAND_SHORT_HELP.get(canonical, "")
            table.add_row(canonical, alias_str, description)

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
    ensure_utf8_stdio()
    cli()
