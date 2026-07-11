"""Real library API for programmatic lintro invocation.

This module exposes :func:`check`, :func:`format` (aliased :func:`fmt`), and
:func:`test` as genuine Python functions that perform the work directly by
delegating to :func:`lintro.utils.tool_executor.run_lint_tools_simple`.

Unlike the previous approach that routed programmatic calls through
``click.testing.CliRunner``, these functions:

- Return a structured :class:`LintroResult` instead of a Click ``Result``.
- Let exceptions propagate to the caller instead of swallowing them.
- Do not redirect or buffer stdout/stderr, so live output stays visible.

The Click commands and the backward-compatible programmatic wrappers in
``lintro.cli_utils.commands`` are thin layers over this API.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.enums.action import Action
from lintro.utils.tool_executor import run_lint_tools_simple

# Constants
DEFAULT_PATHS: list[str] = ["."]


@dataclass(frozen=True)
class LintroResult:
    """Structured result of a programmatic lintro invocation.

    Attributes:
        action: The action that was performed ("check", "fmt", or "test").
        exit_code: Aggregated process exit code (0 on success, non-zero when
            issues were found or an execution error occurred).
    """

    action: str
    exit_code: int

    @property
    def success(self) -> bool:
        """Whether the invocation completed without issues.

        Returns:
            bool: ``True`` when :attr:`exit_code` is zero.
        """
        return self.exit_code == 0


def _ensure_pytest_prefix(option_fragment: str) -> str:
    """Normalize tool option fragments to use the pytest prefix.

    Args:
        option_fragment: Raw option fragment from ``tool_options``.

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


def check(
    *,
    paths: list[str] | tuple[str, ...] | None = None,
    tools: str | None = None,
    tool_options: str | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    output: str | None = None,
    output_format: str = "grid",
    group_by: str = "file",
    ignore_conflicts: bool = False,
    verbose: bool = False,
    no_log: bool = False,
    raw_output: bool = False,
    incremental: bool = False,
    diff_base: str | None = None,
    stream: bool = False,
    debug: bool = False,
    auto_install: bool = False,
    yes: bool = False,
    ai_fix: bool = False,
    transport: str | None = None,
    score: bool = False,
    fail_under: float | None = None,
) -> LintroResult:
    """Check files for issues using the specified tools.

    Args:
        paths: File/directory paths to check. Defaults to the current directory.
        tools: Comma-separated list of tool names to run, or ``"all"``.
        tool_options: Tool-specific configuration options.
        exclude: Comma-separated patterns of files/dirs to exclude.
        include_venv: Whether to include virtual environment directories.
        output: Path to an output file for results.
        output_format: Format for displaying results (grid, json, etc).
        group_by: How to group issues in output (file, code, none, auto).
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        verbose: Whether to show verbose output during execution.
        no_log: Whether to disable logging to file.
        raw_output: Whether to show raw tool output instead of formatted output.
        incremental: Whether to only check files changed since the last run.
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        stream: Whether to stream tool output in real-time.
        debug: Whether to enable debug output on console.
        auto_install: Whether to auto-install Node.js deps if missing.
        yes: Skip confirmation prompt and proceed immediately.
        ai_fix: Generate AI fix suggestions.
        transport: Override AI transport (``api`` or ``cli``).
        score: Print only the health score, suppressing the summary.
        fail_under: Exit non-zero if the health score is below this value.

    Returns:
        LintroResult: Structured result carrying the aggregated exit code.
    """
    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    exit_code: int = run_lint_tools_simple(
        action=Action.CHECK,
        paths=path_list,
        tools=tools,
        tool_options=tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        output_file=output,
        incremental=incremental,
        diff_base=diff_base,
        debug=debug,
        stream=stream,
        no_log=no_log,
        auto_install=auto_install,
        yes=yes,
        ai_fix=ai_fix,
        ignore_conflicts=ignore_conflicts,
        transport=transport,
        score=score,
        fail_under=fail_under,
    )
    return LintroResult(action="check", exit_code=exit_code)


def format(
    *,
    paths: list[str] | tuple[str, ...] | None = None,
    tools: str | None = None,
    tool_options: str | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    group_by: str = "auto",
    output: str | None = None,
    output_format: str = "grid",
    verbose: bool = False,
    no_log: bool = False,
    raw_output: bool = False,
    diff_base: str | None = None,
    stream: bool = False,
    debug: bool = False,
    auto_install: bool = False,
    yes: bool = False,
    dry_run: bool = False,
) -> LintroResult:
    """Format code using the configured formatting tools.

    Args:
        paths: File/directory paths to format. Defaults to the current directory.
        tools: Comma-separated list of tool names to run, or ``"all"``.
        tool_options: Tool-specific configuration options.
        exclude: Comma-separated patterns of files/dirs to exclude.
        include_venv: Whether to include virtual environment directories.
        group_by: How to group issues in output (file, code, none, auto).
        output: Path to an output file for results.
        output_format: Format for displaying results (grid, json, etc).
        verbose: Whether to show verbose output during execution.
        no_log: Whether to disable logging to file.
        raw_output: Whether to show raw tool output instead of formatted output.
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        stream: Whether to stream tool output in real-time.
        debug: Whether to enable debug output on console.
        auto_install: Whether to auto-install Node.js deps if missing.
        yes: Skip confirmation prompt and proceed immediately.
        dry_run: Preview would-be fixes without modifying any files.

    Returns:
        LintroResult: Structured result carrying the aggregated exit code.
    """
    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    exit_code: int = run_lint_tools_simple(
        action="fmt",
        paths=path_list,
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
    )
    return LintroResult(action="fmt", exit_code=exit_code)


# Alias: ``fmt`` is the canonical CLI verb; ``format`` mirrors the CLI command
# name while avoiding surprises for callers who prefer the shorter spelling.
fmt = format


def test(
    *,
    paths: list[str] | tuple[str, ...] | None = None,
    exclude: str | None = None,
    include_venv: bool = False,
    output: str | None = None,
    output_format: str = "grid",
    group_by: str = "file",
    verbose: bool = False,
    raw_output: bool = False,
    tool_options: str | None = None,
    list_plugins: bool = False,
    check_plugins: bool = False,
    collect_only: bool = False,
    fixtures: bool = False,
    fixture_info: str | None = None,
    markers: bool = False,
    parametrize_help: bool = False,
    coverage: bool = False,
    debug: bool = False,
    yes: bool = False,
) -> LintroResult:
    """Run tests using pytest through lintro's output formatting.

    Args:
        paths: Paths to test files or directories. Defaults to the current dir.
        exclude: Comma-separated patterns of files/dirs to exclude.
        include_venv: Whether to include virtual environment directories.
        output: Path to an output file for results.
        output_format: Format for displaying results (grid, json, etc).
        group_by: How to group issues in output (file, code, none, auto).
        verbose: Whether to show verbose output during execution.
        raw_output: Whether to show raw tool output instead of formatted output.
        tool_options: Tool-specific options in ``option=value`` form.
        list_plugins: List all installed pytest plugins.
        check_plugins: Check whether required plugins are installed.
        collect_only: List tests without executing them.
        fixtures: List all available fixtures.
        fixture_info: Show detailed information about a specific fixture.
        markers: List all available markers.
        parametrize_help: Show help for parametrized tests.
        coverage: Generate a test coverage report with missing lines.
        debug: Whether to enable debug output on console.
        yes: Skip confirmation prompt and proceed immediately.

    Returns:
        LintroResult: Structured result carrying the aggregated exit code.
    """
    path_list: list[str] = list(paths) if paths else list(DEFAULT_PATHS)

    tool_option_parts: list[str] = []

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

    if fixture_info:
        tool_option_parts.append(f"pytest:fixture_info={fixture_info}")

    if tool_options:
        # Parse options carefully to handle values containing commas, mirroring
        # the CLI's ``--tool-options`` handling. Format: ``key=value,key=value``
        # where values may themselves contain commas.
        prefixed_options: list[str] = []
        parts = tool_options.split(",")
        i = 0

        while i < len(parts):
            current_part = parts[i].strip()
            if not current_part:
                i += 1
                continue

            if "=" in current_part or current_part.lower().startswith("pytest:"):
                normalized_part = _ensure_pytest_prefix(current_part)
                prefixed_options.append(normalized_part)
                i += 1
            else:
                if prefixed_options and "=" in prefixed_options[-1]:
                    prefixed_options[-1] = f"{prefixed_options[-1]},{current_part}"
                else:
                    prefixed_options.append(_ensure_pytest_prefix(current_part))
                i += 1

        tool_option_parts.append(",".join(prefixed_options))

    combined_tool_options: str | None = (
        ",".join(tool_option_parts) if tool_option_parts else None
    )

    exit_code: int = run_lint_tools_simple(
        action=Action.TEST,
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
    return LintroResult(action="test", exit_code=exit_code)
