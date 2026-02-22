"""Ruff fix execution logic.

Functions for running ruff fix commands and processing results.
"""

import subprocess  # nosec B404 - subprocess used safely to execute ruff commands with controlled input
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from loguru import logger

from lintro.parsers.ruff.ruff_parser import (
    parse_ruff_format_check_output,
    parse_ruff_output,
)
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    get_timeout_value,
)
from lintro.utils.path_filtering import walk_files_with_excludes

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.definitions.ruff import RuffPlugin

# Constants from tool_ruff.py
RUFF_DEFAULT_TIMEOUT: int = 30
DEFAULT_REMAINING_ISSUES_DISPLAY: int = 5


@contextmanager
def _temporary_option(
    tool: "RuffPlugin",
    option_key: str,
    option_value: object,
) -> Generator[None]:
    """Context manager for temporarily setting a tool option.

    Safely mutates tool.options for the duration of the context, ensuring
    the original value is always restored even if an exception occurs.

    Args:
        tool: RuffTool instance whose options will be temporarily modified.
        option_key: Key of the option to temporarily set.
        option_value: Value to temporarily set for the option.

    Yields:
        None: The context manager yields control after setting the option.

    Example:
        >>> with _temporary_option(tool, "unsafe_fixes", True):
        ...     # tool.options["unsafe_fixes"] is now True
        ...     build_command(tool)
        >>> # tool.options["unsafe_fixes"] is restored to original value
    """
    # Check if key existed before (distinguishes None value from missing key)
    key_existed = option_key in tool.options
    original_value = tool.options.get(option_key)
    try:
        tool.options[option_key] = option_value
        yield
    finally:
        # Always restore the original state, even if an exception occurs
        if key_existed:
            tool.options[option_key] = original_value
        elif option_key in tool.options:
            # Remove the key if it wasn't originally present
            del tool.options[option_key]


def execute_ruff_fix(
    tool: "RuffPlugin",
    paths: list[str],
) -> "ToolResult":
    """Execute ruff fix command and process results.

    Args:
        tool: RuffTool instance
        paths: list[str]: List of file or directory paths to fix.

    Returns:
        ToolResult: ToolResult instance.
    """
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.implementations.ruff.commands import (
        build_ruff_check_command,
        build_ruff_format_command,
    )

    # Check version requirements
    version_result = tool._verify_tool_version()
    if version_result is not None:
        return version_result

    tool._validate_paths(paths=paths)
    if not paths:
        return ToolResult(
            name=tool.definition.name,
            success=True,
            output="No files to fix.",
            issues_count=0,
        )

    # Use shared utility for file discovery
    python_files: list[str] = walk_files_with_excludes(
        paths=paths,
        file_patterns=tool.definition.file_patterns,
        exclude_patterns=tool.exclude_patterns,
        include_venv=tool.include_venv,
        incremental=tool.options.get("incremental", False),
        tool_name="ruff",
    )

    if not python_files:
        return ToolResult(
            name=tool.definition.name,
            success=True,
            output="No Python files found to fix.",
            issues_count=0,
        )

    timeout: int = get_timeout_value(tool, RUFF_DEFAULT_TIMEOUT)
    overall_success: bool = True

    # Track unsafe fixes for internal decisioning; do not emit as user-facing noise
    unsafe_fixes_enabled: bool = bool(tool.options.get("unsafe_fixes", False))

    # First, count issues before fixing
    cmd_check: list[str] = build_ruff_check_command(
        tool=tool,
        files=python_files,
        fix=False,
    )
    success_check: bool = False
    output_check: str = ""
    try:
        success_check, output_check = tool._run_subprocess(
            cmd=cmd_check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        timeout_result = create_timeout_result(
            tool=tool,
            timeout=timeout,
            cmd=cmd_check,
        )
        return ToolResult(
            name=tool.definition.name,
            success=timeout_result.success,
            output=timeout_result.output,
            issues_count=timeout_result.issues_count,
            issues=timeout_result.issues,
            initial_issues_count=None,
            fixed_issues_count=None,
            remaining_issues_count=None,
        )
    initial_issues = parse_ruff_output(output=output_check)
    initial_count: int = len(initial_issues)

    # Also check formatting issues before fixing
    initial_format_count: int = 0
    format_files: list[str] = []
    if tool.options.get("format", False):
        format_cmd_check: list[str] = build_ruff_format_command(
            tool=tool,
            files=python_files,
            check_only=True,
        )
        success_format_check: bool = False
        output_format_check: str = ""
        try:
            success_format_check, output_format_check = tool._run_subprocess(
                cmd=format_cmd_check,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"Ruff execution timed out ({timeout}s limit exceeded).\n\n"
                "This may indicate:\n"
                "  - Large codebase taking too long to process\n"
                "  - Need to increase timeout via --tool-options ruff:timeout=N"
            )
            return ToolResult(
                name=tool.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=1,  # Count timeout as execution failure
                # Include any lint issues found before timeout
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=1,
            )
        format_files = parse_ruff_format_check_output(output=output_format_check)
        initial_format_count = len(format_files)

    # Track initial totals separately for accurate fixed/remaining math
    total_initial_count: int = initial_count + initial_format_count

    # Optionally run ruff check --fix (lint fixes)
    remaining_issues = []
    remaining_count = 0
    success: bool = True  # Default to True when lint_fix is disabled
    if tool.options.get("lint_fix", True):
        cmd: list[str] = build_ruff_check_command(
            tool=tool,
            files=python_files,
            fix=True,
        )
        output: str = ""
        try:
            success, output = tool._run_subprocess(cmd=cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"Ruff execution timed out ({timeout}s limit exceeded).\n\n"
                "This may indicate:\n"
                "  - Large codebase taking too long to process\n"
                "  - Need to increase timeout via --tool-options ruff:timeout=N"
            )
            return ToolResult(
                name=tool.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=1,  # Count timeout as execution failure
                issues=initial_issues,  # Include initial issues found
                initial_issues_count=total_initial_count,
                fixed_issues_count=0,
                remaining_issues_count=1,
            )
        remaining_issues = parse_ruff_output(output=output)
        remaining_count = len(remaining_issues)

    # Compute fixed lint issues by diffing initial vs remaining (internal only)
    # Not used for display; summary counts reflect totals.

    # Calculate how many lint issues were actually fixed
    fixed_lint_count: int = max(0, initial_count - remaining_count)
    fixed_count: int = fixed_lint_count

    # If there are remaining issues, check if any are fixable with unsafe fixes
    # If unsafe fixes are disabled, check if any remaining issues are
    # fixable with unsafe fixes
    if remaining_count > 0 and not unsafe_fixes_enabled:
        # Try running ruff with unsafe fixes in dry-run mode to see if it
        # would fix more
        # Use context manager to safely temporarily enable unsafe_fixes
        remaining_unsafe = remaining_issues
        success_unsafe: bool = False
        output_unsafe: str = ""
        with _temporary_option(tool, "unsafe_fixes", True):
            # Build command with unsafe_fixes temporarily enabled
            cmd_unsafe: list[str] = build_ruff_check_command(
                tool=tool,
                files=python_files,
                fix=True,
            )
            # Command is built, option will be restored by context manager
        # Run the command (option already restored)
        try:
            success_unsafe, output_unsafe = tool._run_subprocess(
                cmd=cmd_unsafe,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # If unsafe check times out, just continue with current results
            # Don't fail the entire operation for this optional check
            pass
        else:
            remaining_unsafe = parse_ruff_output(output=output_unsafe)
        if len(remaining_unsafe) < remaining_count:
            logger.warning(
                "Some remaining issues could be fixed by enabling unsafe "
                "fixes (use --tool-options ruff:unsafe_fixes=True)",
            )
    # Log remaining issues for debugging (if verbose)
    # Note: Issue details are already included in the ToolResult.issues list

    if not (success and remaining_count == 0):
        overall_success = False

    # Run ruff format if enabled (default: True)
    if tool.options.get("format", False):
        format_cmd: list[str] = build_ruff_format_command(
            tool=tool,
            files=python_files,
            check_only=False,
        )
        format_success: bool = False
        format_output: str = ""
        try:
            format_success, format_output = tool._run_subprocess(
                cmd=format_cmd,
                timeout=timeout,
            )
            # For ruff format, exit code 1 means files were formatted (success)
            # Only consider it a failure if there were no initial format issues
            if not format_success and initial_format_count > 0:
                format_success = True
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"Ruff execution timed out ({timeout}s limit exceeded).\n\n"
                "This may indicate:\n"
                "  - Large codebase taking too long to process\n"
                "  - Need to increase timeout via --tool-options ruff:timeout=N"
            )
            return ToolResult(
                name=tool.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=1,  # Count timeout as execution failure
                issues=remaining_issues,  # Include any issues found before timeout
                initial_issues_count=total_initial_count,
                fixed_issues_count=fixed_lint_count,
                remaining_issues_count=1,
            )
        # Formatting fixes are counted separately from lint fixes
        if initial_format_count > 0:
            fixed_count = fixed_lint_count + initial_format_count
        # Only consider formatting failure if there are actual formatting
        # issues. Don't fail the overall operation just because formatting
        # failed when there are no issues
        if not format_success and total_initial_count > 0:
            overall_success = False

    # Build concise, unified summary output for fmt runs
    summary_lines: list[str] = []
    if fixed_count > 0:
        summary_lines.append(f"Fixed {fixed_count} issue(s)")
    if remaining_count > 0:
        summary_lines.append(
            f"Found {remaining_count} issue(s) that cannot be auto-fixed",
        )
    final_output: str = (
        "\n".join(summary_lines) if summary_lines else "No fixes applied."
    )

    return ToolResult(
        name=tool.definition.name,
        success=overall_success,
        output=final_output,
        # For fix operations, issues_count represents remaining issues
        # that couldn't be fixed
        issues_count=remaining_count,
        # Only include remaining issues that couldn't be fixed
        # (not successful format operations)
        issues=remaining_issues,
        initial_issues_count=total_initial_count,
        fixed_issues_count=fixed_count,
        remaining_issues_count=remaining_count,
    )
