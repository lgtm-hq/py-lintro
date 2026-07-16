"""Format command implementation using simplified Loguru-based approach."""

import click
from click.testing import CliRunner

from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL
from lintro.utils.tool_executor import run_lint_tools_simple

# Constants
DEFAULT_PATHS: list[str] = ["."]
DEFAULT_EXIT_CODE: int = 0
DEFAULT_ACTION: str = "fmt"


@click.command()
@click.pass_context
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--tools",
    default=None,
    help="Comma-separated list of tools to run (e.g., ruff,black) or 'all'.",
)
@click.option(
    "--tool-options",
    default=None,
    help="Tool-specific options in format tool:option=value,tool2:option=value.",
)
@click.option(
    "--exclude",
    default=None,
    help="Comma-separated patterns to exclude from formatting.",
)
@click.option(
    "--include-venv",
    is_flag=True,
    default=False,
    help="Include virtual environment directories in formatting.",
)
@click.option(
    "--group-by",
    default="auto",
    type=click.Choice(["file", "code", "none", "auto"]),
    help="How to group issues in output.",
)
@click.option(
    "--output",
    type=click.Path(),
    help="Output file path for writing results.",
)
@click.option(
    "--output-format",
    default="grid",
    type=click.Choice(["plain", "grid", "markdown", "html", "json", "csv", "github"]),
    help="Output format for displaying results.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose output with debug information.",
)
@click.option(
    "--no-log",
    is_flag=True,
    default=False,
    help="Disable logging to file.",
)
@click.option(
    "--raw-output",
    is_flag=True,
    default=False,
    help="Show raw tool output instead of formatted output.",
)
@click.option(
    "--diff",
    "diff_base",
    is_flag=False,
    flag_value=DIFF_DEFAULT_SENTINEL,
    default=None,
    metavar="[BASE]",
    help=(
        "Only format files changed vs a git base ref. With no value, diffs "
        "against the repository default branch (origin/HEAD); pass a ref like "
        "'main' or 'origin/dev' to override. Falls back to a full scan outside "
        "a git repository."
    ),
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
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Preview what would be fixed without modifying any files. Lists the "
        "issues each tool would fix and a summary count. Exits 0 when nothing "
        "would be fixed and 1 when fixes are available (useful for CI checks)."
    ),
)
@click.option(
    "--profile",
    is_flag=True,
    help="Show a per-tool performance profile (timing table + suggestions)",
)
def format_command(
    ctx: click.Context,
    paths: tuple[str, ...],
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
    include_venv: bool,
    output: str | None,
    group_by: str,
    output_format: str,
    verbose: bool,
    no_log: bool,
    raw_output: bool,
    diff_base: str | None,
    stream: bool,
    debug: bool,
    auto_install: bool,
    yes: bool,
    dry_run: bool,
    profile: bool,
) -> None:
    """Format code using configured formatting tools.

    Runs code formatting tools on the specified paths to automatically fix style issues.
    Uses simplified Loguru-based logging for clean output and proper file logging.

    Args:
        ctx: click.Context: Click context object for command execution.
        paths: tuple[str, ...]:
            Paths to format (defaults to current directory if none provided).
        tools: str | None: Specific tools to run, or 'all' for all available tools.
        tool_options: str | None: Tool-specific configuration options.
        exclude: str | None: Patterns to exclude from formatting.
        include_venv: bool: Whether to include virtual environment directories.
        output: str | None: Path to output file for results.
        group_by: str: How to group issues in the output display.
        output_format: str: Format for displaying results.
        verbose: bool: Enable detailed debug output.
        no_log: bool: Whether to disable logging to file.
        raw_output: bool: Show raw tool output instead of formatted output.
        diff_base: str | None: Git base ref for ``--diff`` scanning. ``None``
            formats all files; the default sentinel resolves the repo default
            branch; any other value is used as the base ref.
        stream: bool: Whether to stream tool output in real-time.
        debug: bool: Whether to enable debug output on console.
        auto_install: bool: Whether to auto-install Node.js deps if missing.
        yes: bool: Skip confirmation prompt and proceed immediately.
        dry_run: bool: Preview would-be fixes without modifying any files.
        profile: bool: Whether to emit a per-tool performance profile.
    """
    # Default to current directory if no paths provided
    normalized_paths: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    # Run with simplified approach
    exit_code: int = run_lint_tools_simple(
        action=DEFAULT_ACTION,
        paths=normalized_paths,
        tools=tools,
        tool_options=tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        output_file=output,
        diff_base=diff_base,
        debug=debug,
        stream=stream,
        no_log=no_log,
        auto_install=auto_install,
        yes=yes,
        dry_run=dry_run,
        profile=profile,
    )

    # Exit with code from tool execution.
    # For a normal fmt action, exit_code is 1 only if there were execution
    # errors (not if issues were found and fixed - that's success). In
    # --dry-run mode nothing is written and exit_code follows check semantics:
    # 0 when nothing would be fixed, 1 when fixes are available.
    ctx.exit(exit_code)


def format_code(
    paths: list[str] | None = None,
    tools: str | None = None,
    tool_options: str | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    group_by: str = "auto",
    output_format: str = "grid",
    verbose: bool = False,
    auto_install: bool = False,
    yes: bool = False,
) -> None:
    """Programmatic format function.

    Args:
        paths: list[str] | None: List of file/directory paths to format.
        tools: str | None: Comma-separated list of tool names to run.
        tool_options: str | None: Tool-specific configuration options.
        exclude: str | None: Comma-separated patterns of files/dirs to exclude.
        include_venv: bool: Whether to include virtual environment directories.
        group_by: str: How to group issues in output (tool, file, etc).
        output_format: str: Format for displaying results (table, json, etc).
        verbose: bool: Whether to show verbose output during execution.
        auto_install: bool: Whether to auto-install Node.js deps if missing.
        yes: bool: Skip confirmation prompt and proceed immediately.

    Returns:
        None: This function does not return a value.

    Raises:
        RuntimeError: If format fails for any reason.
    """
    args: list[str] = []
    if paths:
        args.extend(paths)
    if tools:
        args.extend(["--tools", tools])
    if tool_options:
        args.extend(["--tool-options", tool_options])
    if exclude:
        args.extend(["--exclude", exclude])
    if include_venv:
        args.append("--include-venv")
    if group_by:
        args.extend(["--group-by", group_by])
    if output_format:
        args.extend(["--output-format", output_format])
    if verbose:
        args.append("--verbose")
    if auto_install:
        args.append("--auto-install")
    if yes:
        args.append("--yes")

    runner = CliRunner()
    result = runner.invoke(format_command, args)
    if result.exit_code != DEFAULT_EXIT_CODE:
        raise RuntimeError(f"Format failed: {result.output}")
    return None


# Legacy alias for backward compatibility
format_code_legacy = format_code

# Export the Click command as the main interface
__all__ = ["format_command", "format_code", "format_code_legacy"]
