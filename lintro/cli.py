"""Command-line interface for Lintro."""

# Annotations stay strings so a `TYPE_CHECKING`-only name (`rich.table.Table`
# below) is never evaluated at def time. Without this, importing the CLI raises
# `NameError` on Python < 3.14, which has no PEP 649 lazy annotations.
from __future__ import annotations

import codecs
import contextlib
import importlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

import click

from lintro import __version__
from lintro.utils.logger_setup import setup_cli_logging

if TYPE_CHECKING:
    from rich.table import Table

# Configure loguru for CLI commands (help, version, etc.).
# Only WARNING and above will show. DEBUG logs go to file when tool_executor
# runs. This stays at import time: it must win over loguru's default handler
# before any module that binds `logger` is imported, and it must bind the real
# stderr rather than whichever stream is current when a command first runs.
setup_cli_logging()


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


# Subcommand modules are imported on first use rather than at import time.
# Eagerly importing all sixteen pulls the execution pipeline, the plugin
# registry, pydantic and `lintro.ai` into every `lintro --version` (#1305), so
# the group below resolves a name to its module only when that command is
# actually requested.
#
# Maps the canonical command name to the ``(module, attribute)`` pair holding
# its :class:`click.Command`.
_COMMAND_MODULES: dict[str, tuple[str, str]] = {
    "badge": ("lintro.cli_utils.commands.badge", "badge_command"),
    "check": ("lintro.cli_utils.commands.check", "check_command"),
    "completions": ("lintro.cli_utils.commands.completions", "completions_command"),
    "config": ("lintro.cli_utils.commands.config", "config_command"),
    "deps": ("lintro.cli_utils.commands.deps", "deps_command"),
    "doctor": ("lintro.cli_utils.commands.doctor", "doctor_command"),
    "format": ("lintro.cli_utils.commands.format", "format_command"),
    "init": ("lintro.cli_utils.commands.init", "init_command"),
    "install": ("lintro.cli_utils.commands.install", "install_command"),
    "licenses": ("lintro.cli_utils.commands.licenses", "licenses_command"),
    "list-tools": ("lintro.cli_utils.commands.list_tools", "list_tools_command"),
    "mcp": ("lintro.cli_utils.commands.mcp", "mcp_command"),
    "review": ("lintro.cli_utils.commands.review", "review_command"),
    "test": ("lintro.cli_utils.commands.test", "test_command"),
    "versions": ("lintro.cli_utils.commands.versions", "versions_command"),
    "watch": ("lintro.cli_utils.commands.watch", "watch_command"),
}

# Attribute name this module used to export -> canonical command name. Kept so
# `from lintro.cli import check_command` keeps working for out-of-tree callers;
# see the module-level `__getattr__` below.
_COMMAND_ATTRIBUTES: dict[str, str] = {
    attribute: canonical for canonical, (_, attribute) in _COMMAND_MODULES.items()
}

# Alias name -> canonical command name.
_COMMAND_ALIASES: dict[str, str] = {
    "chk": "check",
    "lint": "check",
    "comp": "completions",
    "cfg": "config",
    "fmt": "format",
    "fix": "format",
    "tst": "test",
    "ls": "list-tools",
    "tools": "list-tools",
    "ins": "install",
    "lic": "licenses",
    "rev": "review",
    "ver": "versions",
    "version": "versions",
    "w": "watch",
}


def _load_command(canonical: str) -> click.Command:
    """Import a canonical command's module and return its Click command.

    Args:
        canonical: Canonical command name present in ``_COMMAND_MODULES``.

    Returns:
        click.Command: The command object, tagged with its canonical name so
        help rendering can group aliases under it.

    Raises:
        TypeError: If the table entry does not name a :class:`click.Command`.
            The static tables are covered per entry by
            ``tests/unit/cli/test_lazy_subcommands.py``, so this can only fire
            on a hand-edited table.
    """
    module_path, attribute = _COMMAND_MODULES[canonical]
    # Module paths come from the static table above, never from user input.
    module = importlib.import_module(module_path)  # nosemgrep: non-literal-import
    command = getattr(module, attribute)
    if not isinstance(command, click.Command):
        raise TypeError(
            f"{module_path}.{attribute} is {type(command).__name__}, "
            f"not a click.Command",
        )
    cast(Any, command)._canonical_name = canonical
    return command


# Ignore-file name used by upward lookup. Kept next to the shared YAML
# filenames so fingerprinting and ``find_lintro_ignore`` cannot drift.
_LINTRO_IGNORE_FILENAME = ".lintro-ignore"

# Truthy values accepted for the LINTRO_NO_CACHE escape hatch.
_TRUTHY_ENV_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

# Fingerprint of the config inputs seen during the previous in-process
# invocation. `None` means no invocation has run yet, so the first call in a
# process always clears the caches and starts fresh.
ConfigSignature = tuple[str, int, int] | None
ConfigFingerprint = tuple[str, str, tuple[ConfigSignature, ...]]
_last_config_fingerprint: ConfigFingerprint | None = None


def _stat_signature(path: Path) -> ConfigSignature:
    """Return a stat-based signature for a config file.

    Args:
        path: The candidate config file to inspect.

    Returns:
        ConfigSignature: A ``(path, size, mtime_ns)`` tuple when the file
        exists and is readable, otherwise ``None``.
    """
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (str(path), stat_result.st_size, stat_result.st_mtime_ns)


def _compute_config_fingerprint() -> ConfigFingerprint:
    """Compute a fingerprint of the config inputs for the current process.

    The fingerprint combines the resolved working directory, ``PATH``, the
    nearest ``pyproject.toml`` (searched upward), and Lintro config / ignore
    files in the working directory and every ancestor. Two invocations that
    produce the same fingerprint may safely reuse the discovery, pyproject,
    and YAML config caches.

    Returns:
        ConfigFingerprint: A hashable, comparable fingerprint of the inputs.
    """
    from lintro.config.config_loader import LINTRO_CONFIG_FILENAMES

    cwd = Path.cwd().resolve()
    signatures: list[ConfigSignature] = []

    # pyproject.toml may live in a parent directory; use the nearest one.
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            signatures.append(_stat_signature(candidate))
            break
    else:
        signatures.append(None)

    for parent in [cwd, *cwd.parents]:
        for filename in LINTRO_CONFIG_FILENAMES:
            signatures.append(_stat_signature(parent / filename))
        signatures.append(_stat_signature(parent / _LINTRO_IGNORE_FILENAME))

    return (str(cwd), os.environ.get("PATH", ""), tuple(signatures))


def _cache_clear_requested_via_env() -> bool:
    """Report whether ``LINTRO_NO_CACHE`` forces cache clearing.

    Returns:
        bool: ``True`` when the ``LINTRO_NO_CACHE`` environment variable is set
        to a truthy value, otherwise ``False``.
    """
    value = os.environ.get("LINTRO_NO_CACHE", "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


def _maybe_clear_caches() -> None:
    """Clear discovery/pyproject caches only when config inputs changed.

    The caches are cleared on the first invocation in a process, whenever the
    config fingerprint differs from the previous invocation (cwd, ``PATH``,
    pyproject, or ancestor Lintro config/ignore files), or when the
    ``LINTRO_NO_CACHE`` escape hatch is enabled. Otherwise the caches are reused
    to avoid redundant filesystem probing and re-parsing.
    """
    from lintro.config.config_loader import clear_config_cache
    from lintro.tools.core.runtime_discovery import clear_discovery_cache
    from lintro.utils.config import clear_pyproject_cache

    global _last_config_fingerprint

    fingerprint = _compute_config_fingerprint()
    if _cache_clear_requested_via_env() or fingerprint != _last_config_fingerprint:
        clear_discovery_cache()
        clear_pyproject_cache()
        clear_config_cache()
    _last_config_fingerprint = fingerprint


class LintroGroup(click.Group):
    """Custom Click group with enhanced help rendering and command chaining.

    This group prints command aliases alongside their canonical names to make
    the CLI help output more discoverable. It also supports command chaining
    with comma-separated commands (e.g., lintro fmt , chk , tst).

    Subcommands resolve lazily: :meth:`get_command` imports a command's module
    the first time that command (or one of its aliases) is requested, so
    ``lintro --version`` never pays for the execution pipeline (#1305).
    """

    def get_command(
        self,
        ctx: click.Context,
        cmd_name: str,
    ) -> click.Command | None:
        """Resolve a command name, importing its module on first use.

        Args:
            ctx: click.Context: The Click context.
            cmd_name: str: The command name or alias to resolve.

        Returns:
            click.Command | None: The resolved command, or ``None`` when the
            name is neither a canonical command nor a known alias.
        """
        existing = super().get_command(ctx, cmd_name)
        if existing is not None:
            return existing

        canonical = _COMMAND_ALIASES.get(cmd_name, cmd_name)
        if canonical not in _COMMAND_MODULES:
            return None

        command = _load_command(canonical)
        # Cache under the canonical name and the requested alias so the next
        # lookup (and ``self.commands``) skips the import entirely.
        self.add_command(command, name=canonical)
        if cmd_name != canonical:
            self.add_command(command, name=cmd_name)
        return command

    def list_commands(
        self,
        ctx: click.Context,
    ) -> list[str]:
        """List every command name, including aliases, without importing them.

        Args:
            ctx: click.Context: The Click context.

        Returns:
            list[str]: Sorted canonical names, aliases, and any command added
            at runtime (for example by a test or a plugin).
        """
        names = set(_COMMAND_MODULES) | set(_COMMAND_ALIASES) | set(self.commands)
        return sorted(names)

    def load_all_commands(
        self,
        ctx: click.Context | None = None,
    ) -> None:
        """Import and register every command, canonical names and aliases.

        Needed by consumers that read ``Group.commands`` directly instead of
        going through :meth:`get_command` — the man-page generator, for one.

        Args:
            ctx: click.Context | None: Context to resolve against; a throwaway
                context is used when omitted.
        """
        context = ctx if ctx is not None else click.Context(self)
        for name in self.list_commands(context):
            self.get_command(context, name)

    def _build_command_table(
        self,
        ctx: click.Context,
    ) -> Table:
        """Build the help table listing every command with its aliases.

        Resolving the names materializes every subcommand, which is why help is
        the one cold path that still pays for the full import (#1305).

        Args:
            ctx: click.Context: The Click context.

        Returns:
            Table: A Rich table of canonical names, aliases, and short help.
        """
        from rich.table import Table

        canonical_map: dict[str, tuple[click.Command, list[str]]] = {}
        for name in self.list_commands(ctx):
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

        return table

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

        console.print(self._build_command_table(ctx))
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

        # Clear the discovery/pyproject caches only when the config inputs
        # changed since the last in-process invocation (or when forced via
        # LINTRO_NO_CACHE). This keeps single-shot CLI semantics intact while
        # avoiding redundant tool detection and pyproject.toml re-parsing when
        # nothing relevant changed.
        _maybe_clear_caches()

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


def main() -> None:
    """Entry point for the CLI."""
    ensure_utf8_stdio()
    cli()


def __getattr__(name: str) -> Any:
    """Resolve a command object that this module used to export eagerly.

    Subcommands load on demand (#1305), so ``badge_command`` and friends are no
    longer module attributes. Out-of-tree callers that imported them keep
    working: the first access imports the owning module and caches the result
    in this module's namespace.

    Args:
        name: Attribute being looked up.

    Returns:
        Any: The resolved :class:`click.Command`.

    Raises:
        AttributeError: If *name* is not one of the historical command exports.
    """
    canonical = _COMMAND_ATTRIBUTES.get(name)
    if canonical is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    command = _load_command(canonical)
    globals()[name] = command
    return command
