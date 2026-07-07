"""Watch command implementation for the lintro CLI.

Implements ``lintro watch``: monitor paths for filesystem changes and
re-run the relevant tools on the files that changed, with debouncing and a
clean Ctrl-C shutdown.
"""

from __future__ import annotations

import click
from rich.console import Console

from lintro.config.config_loader import load_config
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
    "Defaults to smart selection based on changed file types.",
)
@click.option(
    "--fix",
    "auto_fix",
    is_flag=True,
    default=None,
    help="Automatically fix issues on change instead of only checking.",
)
@click.option(
    "--clear",
    "clear_screen",
    is_flag=True,
    default=None,
    help="Clear the screen between runs for cleaner output.",
)
@click.option(
    "--debounce",
    "debounce_ms",
    type=int,
    default=None,
    help="Debounce interval in milliseconds before re-running (default: 300).",
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
    type=click.Choice(["plain", "grid", "markdown", "json", "csv"]),
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
    watch_cfg = load_config().watch

    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    # CLI flags override config; config overrides built-in defaults.
    effective_debounce = (
        debounce_ms if debounce_ms is not None else watch_cfg.debounce_ms
    )
    effective_fix = auto_fix if auto_fix is not None else watch_cfg.auto_fix
    effective_clear = (
        clear_screen if clear_screen is not None else watch_cfg.clear_screen
    )
    restrict_to: list[str] | None = None
    if tools:
        restrict_to = [name.strip() for name in tools.split(",") if name.strip()]
    elif watch_cfg.tools:
        restrict_to = list(watch_cfg.tools)

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
    )

    watch_paths(
        path_list,
        on_batch=runner.run_batch,
        debounce_ms=effective_debounce,
        ignore_patterns=ignore_patterns,
        console=console,
    )
