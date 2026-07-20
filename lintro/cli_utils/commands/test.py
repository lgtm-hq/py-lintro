"""Test command implementation for running pytest tests."""

from typing import Any, cast

import click

from lintro.api import core as api
from lintro.utils.tool_executor import run_lint_tools_simple

# Constants
DEFAULT_PATHS: list[str] = ["."]
DEFAULT_EXIT_CODE: int = 0
DEFAULT_ACTION: str = "test"


def _ensure_pytest_prefix(option_fragment: str) -> str:
    """Normalize tool option fragments to use the pytest prefix.

    Args:
        option_fragment: Raw option fragment from --tool-options.

    Returns:
        str: Fragment guaranteed to start with ``pytest:``.
    """
    fragment = option_fragment.strip()
    if not fragment:
        return fragment

    lowered = fragment.lower()
    if lowered.startswith("pytest:"):
        _, rest = fragment.split(":", 1)
        return f"pytest:{rest}"
    return f"pytest:{fragment}"


@click.command("test")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--exclude",
    type=str,
    help="Comma-separated list of patterns to exclude from testing",
)
@click.option(
    "--include-venv",
    is_flag=True,
    help="Include virtual environment directories in testing",
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
    type=click.Choice(["file", "code", "none", "auto", "category"]),
    default="file",
    help="How to group issues in the output",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output",
)
@click.option(
    "--raw-output",
    is_flag=True,
    help="Show raw tool output instead of formatted output",
)
@click.option(
    "--tool-options",
    type=str,
    help="Tool-specific options in the format option=value,option=value",
)
@click.option(
    "--list-plugins",
    is_flag=True,
    default=False,
    help="List all installed pytest plugins",
)
@click.option(
    "--check-plugins",
    is_flag=True,
    default=False,
    help=(
        "Check if required plugins are installed "
        "(use with --tool-options pytest:required_plugins=plugin1,plugin2)"
    ),
)
@click.option(
    "--collect-only",
    is_flag=True,
    default=False,
    help="List tests without executing them",
)
@click.option(
    "--fixtures",
    is_flag=True,
    default=False,
    help="List all available fixtures",
)
@click.option(
    "--fixture-info",
    type=str,
    default=None,
    help="Show detailed information about a specific fixture",
)
@click.option(
    "--markers",
    is_flag=True,
    default=False,
    help="List all available markers",
)
@click.option(
    "--parametrize-help",
    is_flag=True,
    default=False,
    help="Show help for parametrized tests",
)
@click.option(
    "--coverage",
    is_flag=True,
    default=False,
    help="Generate test coverage report with missing lines shown in terminal",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug output on console",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt and proceed immediately",
)
def test_command(
    paths: tuple[str, ...],
    exclude: str | None,
    include_venv: bool,
    output: str | None,
    output_format: str,
    group_by: str,
    verbose: bool,
    raw_output: bool,
    tool_options: str | None,
    list_plugins: bool,
    check_plugins: bool,
    collect_only: bool,
    fixtures: bool,
    fixture_info: str | None,
    markers: bool,
    parametrize_help: bool,
    coverage: bool,
    debug: bool,
    yes: bool,
) -> None:
    """Run tests using pytest.

    This CLI command wraps pytest with lintro's output formatting.

    Args:
        paths: Paths to test files or directories.
        exclude: Pattern to exclude paths.
        include_venv: Whether to include virtual environment directories.
        output: Output file path.
        output_format: Output format for displaying results.
        group_by: How to group issues in the output.
        verbose: Show verbose output.
        raw_output: Show raw tool output instead of formatted output.
        tool_options: Tool-specific options in the format option=value.
        list_plugins: List all installed pytest plugins.
        check_plugins: Check if required plugins are installed.
        collect_only: List tests without executing them.
        fixtures: List all available fixtures.
        fixture_info: Show detailed information about a specific fixture.
        markers: List all available markers.
        parametrize_help: Show help for parametrized tests.
        coverage: Generate test coverage report with missing lines.
        debug: Enable debug output on console.
        yes: Skip confirmation prompt and proceed immediately.

    Raises:
        SystemExit: Process exit with the aggregated exit code.
    """
    # Add default paths if none provided
    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    # Build tool options with pytest prefix
    tool_option_parts: list[str] = []

    # Add special mode flags
    boolean_flags: list[tuple[bool, str]] = [
        (list_plugins, "pytest:list_plugins=True"),
        (check_plugins, "pytest:check_plugins=True"),
        (collect_only, "pytest:collect_only=True"),
        (fixtures, "pytest:list_fixtures=True"),
        (markers, "pytest:list_markers=True"),
        (parametrize_help, "pytest:parametrize_help=True"),
        (coverage, "pytest:coverage_term_missing=True"),
    ]

    for flag_value, option_string in boolean_flags:
        if flag_value:
            tool_option_parts.append(option_string)

    # Handle fixture_info as special case (requires non-empty value)
    if fixture_info:
        tool_option_parts.append(f"pytest:fixture_info={fixture_info}")

    if tool_options:
        # Prefix with "pytest:" for pytest tool
        # Parse options carefully to handle values containing commas
        # Format: key=value,key=value where values can contain commas
        prefixed_options: list[str] = []
        parts = tool_options.split(",")
        i = 0

        while i < len(parts):
            current_part = parts[i].strip()
            if not current_part:
                i += 1
                continue

            # Check if this part looks like a complete option (contains =)
            # or starts with pytest prefix (already namespaced)
            if "=" in current_part or current_part.lower().startswith("pytest:"):
                normalized_part = _ensure_pytest_prefix(current_part)
                prefixed_options.append(normalized_part)
                i += 1
            else:
                # This part doesn't have =, might be a value continuation
                # Merge with previous part if it exists and had an =
                if prefixed_options and "=" in prefixed_options[-1]:
                    # Merge with previous option's value
                    prefixed_options[-1] = f"{prefixed_options[-1]},{current_part}"
                else:
                    # Standalone option without =, prefix it
                    prefixed_options.append(_ensure_pytest_prefix(current_part))
                i += 1

        tool_option_parts.append(",".join(prefixed_options))

    combined_tool_options: str | None = (
        ",".join(tool_option_parts) if tool_option_parts else None
    )

    # Run with pytest tool
    exit_code: int = run_lint_tools_simple(
        action=DEFAULT_ACTION,
        paths=path_list,
        tools="pytest",
        tool_options=combined_tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        output_file=output,
        debug=debug,
        yes=yes,
    )

    # Exit with code only
    raise SystemExit(exit_code)


# Exclude from pytest collection - this is a Click command, not a test function
cast(Any, test_command).__test__ = False


def test(
    paths: tuple[str, ...],
    exclude: str | None,
    include_venv: bool,
    output: str | None,
    output_format: str,
    group_by: str,
    verbose: bool,
    raw_output: bool = False,
    tool_options: str | None = None,
    yes: bool = False,
) -> None:
    """Programmatic test function for backward compatibility.

    Args:
        paths: tuple: List of file/directory paths to test.
        exclude: str | None: Comma-separated patterns of files/dirs to exclude.
        include_venv: bool: Whether to include virtual environment directories.
        output: str | None: Path to output file for results.
        output_format: str: Format for displaying results.
        group_by: str: How to group issues in output.
        verbose: bool: Whether to show verbose output during execution.
        raw_output: bool: Whether to show raw tool output instead of formatted output.
        tool_options: str | None: Tool-specific options.
        yes: bool: Skip confirmation prompt and proceed immediately.

    Returns:
        None: This function does not return a value.
    """
    result = api.test(
        paths=paths,
        exclude=exclude,
        include_venv=include_venv,
        output=output,
        output_format=output_format,
        group_by=group_by,
        verbose=verbose,
        raw_output=raw_output,
        tool_options=tool_options,
        yes=yes,
    )

    if result.exit_code != DEFAULT_EXIT_CODE:
        import sys

        sys.exit(result.exit_code)
    return None
