"""Check command implementation for lintro CLI.

This module provides the core logic for the 'check' command.

Functions:
    check_command: CLI command for checking files with various tools.
    check: Programmatic function for backward compatibility.
"""

import sys

import click
from click.testing import CliRunner

from lintro.utils.tool_executor import run_lint_tools_simple

# Constants
DEFAULT_PATHS: list[str] = ["."]
DEFAULT_EXIT_CODE: int = 0
DEFAULT_ACTION: str = "check"


@click.command("check")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--tools",
    type=str,
    help='Comma-separated list of tools to run. Use "all" to run all available tools.',
)
@click.option(
    "--tool-options",
    type=str,
    help="Tool-specific options in the format tool:option=value,tool:option=value",
)
@click.option(
    "--exclude",
    type=str,
    help="Comma-separated list of patterns to exclude from processing",
)
@click.option(
    "--include-venv",
    is_flag=True,
    help="Include virtual environment directories in processing",
)
@click.option(
    "--output",
    type=click.Path(),
    help="Output file path for writing results",
)
@click.option(
    "--output-format",
    type=click.Choice(["plain", "grid", "markdown", "html", "json", "csv", "github"]),
    default="grid",
    help="Output format for displaying results",
)
@click.option(
    "--group-by",
    type=click.Choice(["file", "code", "none", "auto"]),
    default="file",
    help="How to group issues in the output",
)
@click.option(
    "--ignore-conflicts",
    is_flag=True,
    help="Ignore potential conflicts between tools",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output",
)
@click.option(
    "--no-log",
    is_flag=True,
    help="Disable logging to file",
)
@click.option(
    "--raw-output",
    is_flag=True,
    help="Show raw tool output instead of formatted output",
)
@click.option(
    "--incremental",
    is_flag=True,
    help="Only check files that have changed since the last run",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Clear incremental cache before running (forces full check)",
)
@click.option(
    "--stream/--no-stream",
    default=False,
    help="Stream tool output in real-time (useful for long operations)",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug output on console",
)
@click.option(
    "--auto-install",
    is_flag=True,
    help="Auto-install Node.js dependencies if node_modules is missing",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt and proceed immediately",
)
@click.option(
    "--fix",
    "ai_fix",
    is_flag=True,
    help="Generate AI fix suggestions with interactive accept/reject",
)
def check_command(
    paths: tuple[str, ...],
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
    include_venv: bool,
    output: str | None,
    output_format: str,
    group_by: str,
    ignore_conflicts: bool,
    verbose: bool,
    no_log: bool,
    raw_output: bool,
    incremental: bool,
    no_cache: bool,
    stream: bool,
    debug: bool,
    auto_install: bool,
    yes: bool,
    ai_fix: bool,
) -> None:
    """Check files for issues using the specified tools.

    Args:
        paths: tuple: List of file/directory paths to check.
        tools: str | None: Comma-separated list of tool names to run.
        tool_options: str | None: Tool-specific configuration options.
        exclude: str | None: Comma-separated patterns of files/dirs to exclude.
        include_venv: bool: Whether to include virtual environment directories.
        output: str | None: Path to output file for results.
        output_format: str: Format for displaying results (table, json, etc).
        group_by: str: How to group issues in output (tool, file, etc).
        ignore_conflicts: bool: Whether to ignore tool configuration conflicts.
        verbose: bool: Whether to show verbose output during execution.
        no_log: bool: Whether to disable logging to file.
        raw_output: bool: Whether to show raw tool output instead of formatted output.
        incremental: bool: Whether to only check files changed since last run.
        no_cache: bool: Whether to clear the incremental cache before running.
        stream: bool: Whether to stream tool output in real-time.
        debug: bool: Whether to enable debug output on console.
        auto_install: bool: Whether to auto-install Node.js deps if missing.
        yes: bool: Skip confirmation prompt and proceed immediately.
        ai_fix: bool: Generate AI fix suggestions with interactive review.

    Raises:
        SystemExit: Process exit with the aggregated exit code from tools.
    """
    # Handle cache clearing
    if no_cache:
        from lintro.utils.file_cache import clear_all_caches

        clear_all_caches()

    # Add default paths if none provided
    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    # Build tool-specific options string
    tool_option_parts: list[str] = []
    if tool_options:
        tool_option_parts.append(tool_options)

    combined_tool_options: str | None = (
        ",".join(tool_option_parts) if tool_option_parts else None
    )

    # Run with simplified approach
    exit_code: int = run_lint_tools_simple(
        action=DEFAULT_ACTION,
        paths=path_list,
        tools=tools,
        tool_options=combined_tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        output_file=output,
        incremental=incremental,
        debug=debug,
        stream=stream,
        no_log=no_log,
        auto_install=auto_install,
        yes=yes,
        ai_fix=ai_fix,
    )

    # Exit with code only; CLI uses this as process exit code and avoids any
    # additional trailing output after the logger's ASCII art.
    raise SystemExit(exit_code)


def check(
    paths: tuple[str, ...],
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
    include_venv: bool,
    output: str | None,
    output_format: str,
    group_by: str,
    ignore_conflicts: bool,
    verbose: bool,
    no_log: bool,
    auto_install: bool = False,
    yes: bool = False,
) -> None:
    """Programmatic check function for backward compatibility.

    Args:
        paths: tuple: List of file/directory paths to check.
        tools: str | None: Comma-separated list of tool names to run.
        tool_options: str | None: Tool-specific configuration options.
        exclude: str | None: Comma-separated patterns of files/dirs to exclude.
        include_venv: bool: Whether to include virtual environment directories.
        output: str | None: Path to output file for results.
        output_format: str: Format for displaying results (table, json, etc).
        group_by: str: How to group issues in output (tool, file, etc).
        ignore_conflicts: bool: Whether to ignore tool configuration conflicts.
        verbose: bool: Whether to show verbose output during execution.
        no_log: bool: Whether to disable logging to file.
        auto_install: bool: Whether to auto-install Node.js deps if missing.
        yes: bool: Skip confirmation prompt and proceed immediately.

    Returns:
        None: This function does not return a value.
    """
    # Build arguments for the click command
    args: list[str] = []
    if paths:
        args.extend(list(paths))
    if tools:
        args.extend(["--tools", tools])
    if tool_options:
        args.extend(["--tool-options", tool_options])
    if exclude:
        args.extend(["--exclude", exclude])
    if include_venv:
        args.append("--include-venv")
    if output:
        args.extend(["--output", output])
    if output_format:
        args.extend(["--output-format", output_format])
    if group_by:
        args.extend(["--group-by", group_by])
    if ignore_conflicts:
        args.append("--ignore-conflicts")
    if verbose:
        args.append("--verbose")
    if no_log:
        args.append("--no-log")
    if auto_install:
        args.append("--auto-install")
    if yes:
        args.append("--yes")

    runner = CliRunner()
    result = runner.invoke(check_command, args)

    if result.exit_code != DEFAULT_EXIT_CODE:
        sys.exit(result.exit_code)
    return None
