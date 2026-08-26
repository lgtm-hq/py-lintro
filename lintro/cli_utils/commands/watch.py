"""Watch command implementation for the lintro CLI.

Implements ``lintro watch``: monitor paths for filesystem changes and
re-run the relevant tools on the files that changed, with debouncing and a
clean Ctrl-C shutdown.
"""

from __future__ import annotations

import click
from rich.console import Console

from lintro.config.config_loader import load_config
from lintro.enums.action import Action
from lintro.exceptions.errors import ConfigurationError
from lintro.utils.execution.tool_configuration import get_tools_to_run
from lintro.watch.runner import WatchRunner
from lintro.watch.watcher import watch_paths

# Constants
DEFAULT_PATHS: tuple[str, ...] = (".",)


@click.command("watch")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--tools",
    default=None,
    help="Comma-separated list of tools to run (e.g., ruff,mypy). "
    "Defaults to watch.tools, then smart selection based on changed file types. "
    "Use 'all' for smart selection across every enabled tool.",
)
@click.option(
    "--fix/--no-fix",
    "auto_fix",
    default=None,
    help="Automatically fix issues on change instead of only checking. "
    "Use --no-fix to force check-only when config sets watch.auto_fix.",
)
@click.option(
    "--clear/--no-clear",
    "clear_screen",
    default=None,
    help="Clear the screen between runs for cleaner output. "
    "Use --no-clear to keep output when config sets watch.clear_screen.",
)
@click.option(
    "--debounce",
    "debounce_ms",
    type=click.IntRange(min=0),
    default=None,
    help="Debounce interval in milliseconds before re-running. "
    "Defaults to watch.debounce_ms, then 300.",
)
@click.option(
    "--exclude",
    default=None,
    help="Comma-separated patterns to exclude from processing.",
)
@click.option(
    "--include-venv",
    is_flag=True,
    default=False,
    help="Include virtual environment directories in processing.",
)
@click.option(
    "--output-format",
    default="grid",
    type=click.Choice(["plain", "grid"]),
    help="Output format for displaying results.",
)
def watch_command(
    paths: tuple[str, ...],
    tools: str | None,
    auto_fix: bool | None,
    clear_screen: bool | None,
    debounce_ms: int | None,
    exclude: str | None,
    include_venv: bool,
    output_format: str,
) -> None:
    """Watch paths and continuously lint files as they change.

    Runs until interrupted with Ctrl-C. Only tools relevant to the changed
    file types are run, and rapid successive changes are debounced into a
    single run.
    \u000c

    Args:
        paths: Files/directories to watch (defaults to the current directory).
        tools: Optional comma-separated allowlist of tools to run.
        auto_fix: Run tools in fix mode instead of check-only.
        clear_screen: Clear the terminal between runs.
        debounce_ms: Debounce interval in milliseconds.
        exclude: Comma-separated exclude patterns.
        include_venv: Whether to include virtualenv directories.
        output_format: Output format for results.
    """
    console = Console()
    try:
        config = load_config()
    except (ConfigurationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    watch_cfg = config.watch

    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    # CLI flags override config; config overrides built-in defaults.
    effective_debounce = (
        debounce_ms if debounce_ms is not None else watch_cfg.debounce_ms
    )
    effective_fix = auto_fix if auto_fix is not None else watch_cfg.auto_fix
    effective_clear = (
        clear_screen if clear_screen is not None else watch_cfg.clear_screen
    )
    configured_tools = (
        [name.strip() for name in tools.split(",") if name.strip()]
        if tools
        else list(watch_cfg.tools)
    )
    restrict_to: list[str] | None = configured_tools or None
    if restrict_to and len(restrict_to) == 1 and restrict_to[0].lower() == "all":
        restrict_to = None
    if restrict_to is not None:
        action = Action.FIX if effective_fix else Action.CHECK
        try:
            selection = get_tools_to_run(
                tools=",".join(restrict_to),
                action=action,
                lintro_config=config,
                scan_roots=path_list,
            )
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--tools") from exc
        for skipped in selection.skipped:
            console.print(
                f"[yellow]Skipping {skipped.name}: {skipped.reason}[/yellow]",
            )
        restrict_to = selection.to_run
        if not restrict_to:
            raise click.UsageError("No enabled watch tools remain after validation.")

    ignore_patterns = list(watch_cfg.ignore) if watch_cfg.ignore else None

    # Header/notice lines contain literal text like "[12:34:56]" that Rich
    # would otherwise treat as markup, so disable markup for the runner sink.
    def _emit(message: str) -> None:
        console.print(message, markup=False, highlight=False)

    runner = WatchRunner(
        auto_fix=effective_fix,
        clear_screen=effective_clear,
        output_format=output_format,
        restrict_to=restrict_to,
        exclude=exclude,
        include_venv=include_venv,
        emit=_emit,
        watch_paths=path_list,
    )

    try:
        watch_paths(
            path_list,
            on_batch=runner.run_batch,
            on_event=runner.record_event,
            debounce_ms=effective_debounce,
            ignore_patterns=ignore_patterns,
            include_venv=include_venv,
            console=console,
        )
    except (OSError, RuntimeError) as exc:
        raise click.ClickException(f"Watch failed: {exc}") from exc
    if runner.last_exit_code:
        raise click.exceptions.Exit(runner.last_exit_code)
