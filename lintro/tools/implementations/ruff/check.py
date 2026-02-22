"""Ruff check execution logic.

Functions for running ruff check commands and processing results.
"""

import os
import subprocess  # nosec B404 - subprocess used safely to execute ruff commands with controlled input
from typing import TYPE_CHECKING

from loguru import logger

from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.parsers.ruff.ruff_parser import (
    parse_ruff_format_check_output,
    parse_ruff_output,
)
from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    get_timeout_value,
    run_subprocess_with_timeout,
)
from lintro.utils.path_filtering import walk_files_with_excludes

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.definitions.ruff import RuffPlugin

# Default timeout for Ruff operations
RUFF_DEFAULT_TIMEOUT: int = 30


def execute_ruff_check(
    tool: "RuffPlugin",
    paths: list[str],
) -> "ToolResult":
    """Execute ruff check command and process results.

    Args:
        tool: RuffTool instance
        paths: list[str]: List of file or directory paths to check.

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
            output="No files to check.",
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
            output="No Python files found to check.",
            issues_count=0,
        )

    # Ensure Ruff discovers the correct configuration by setting the
    # working directory to the common parent of the target files and by
    # passing file paths relative to that directory.
    cwd: str | None = tool._get_cwd(paths=python_files)
    rel_files: list[str] = []
    for f in python_files:
        if cwd:
            try:
                # Try to get relative path; may fail on Windows
                # if paths are on different drives
                rel_path = os.path.relpath(f, cwd)
                rel_files.append(rel_path)
            except ValueError:
                # Paths are on different drives (Windows) or other error
                # - use absolute path
                rel_files.append(os.path.abspath(f))
        else:
            # No common directory - use absolute paths
            rel_files.append(os.path.abspath(f))

    timeout: int = get_timeout_value(tool, RUFF_DEFAULT_TIMEOUT)
    # Lint check
    cmd: list[str] = build_ruff_check_command(tool=tool, files=rel_files, fix=False)
    success_lint: bool
    output_lint: str
    try:
        success_lint, output_lint = run_subprocess_with_timeout(
            tool=tool,
            cmd=cmd,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        timeout_result = create_timeout_result(
            tool=tool,
            timeout=timeout,
            cmd=cmd,
        )
        return ToolResult(
            name=tool.definition.name,
            success=timeout_result.success,
            output=timeout_result.output,
            issues_count=timeout_result.issues_count,
            issues=timeout_result.issues,
        )

    # Debug logging for CI diagnostics
    logger.debug(f"[ruff] check command: {' '.join(cmd)}")
    logger.debug(f"[ruff] check success: {success_lint}")
    if not success_lint:
        # Log full output to debug file only - raw JSON output is parsed and
        # formatted into tables, so no need to show it in console warnings
        logger.debug(f"[ruff] check full output:\n{output_lint}")

    lint_issues = parse_ruff_output(output=output_lint)
    lint_issues_count: int = len(lint_issues)

    # Optional format check via `format_check` flag
    format_issues_count: int = 0
    format_files: list[str] = []
    format_issues: list[RuffFormatIssue] = []
    success_format: bool = True  # Default to True when format check is skipped
    if tool.options.get("format_check", False):
        format_cmd: list[str] = build_ruff_format_command(
            tool=tool,
            files=rel_files,
            check_only=True,
        )
        output_format: str
        try:
            success_format, output_format = run_subprocess_with_timeout(
                tool=tool,
                cmd=format_cmd,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=tool,
                timeout=timeout,
                cmd=format_cmd,
            )
            return ToolResult(
                name=tool.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=lint_issues_count + timeout_result.issues_count,
                issues=lint_issues + timeout_result.issues,
            )

        # Debug logging for CI diagnostics
        logger.debug(f"[ruff] format --check command: {' '.join(format_cmd)}")
        logger.debug(f"[ruff] format --check success: {success_format}")
        if not success_format:
            # Log full output to debug file only - output is parsed and
            # formatted into tables, so no need to show it in console warnings
            logger.debug(f"[ruff] format check full output:\n{output_format}")

        format_files = parse_ruff_format_check_output(output=output_format)
        # Normalize files to absolute paths to keep behavior consistent with
        # direct CLI calls and stabilize tests that compare exact paths.
        normalized_files: list[str] = []
        for file_path in format_files:
            if cwd and not os.path.isabs(file_path):
                absolute_path = os.path.abspath(os.path.join(cwd, file_path))
                normalized_files.append(absolute_path)
            else:
                normalized_files.append(file_path)
        format_issues_count = len(normalized_files)
        format_issues = [RuffFormatIssue(file=file) for file in normalized_files]

    # Combine results - respect subprocess exit codes and issue counts
    issues_count: int = lint_issues_count + format_issues_count
    success: bool = success_lint and success_format and (issues_count == 0)

    # Diagnostic logging for the "ERROR" case (subprocess failed but no issues parsed)
    if not success and issues_count == 0:
        logger.warning(
            f"ruff subprocess failed (lint={success_lint}, format={success_format}) "
            f"but no issues were parsed - this indicates a ruff execution error",
        )

    # Suppress narrative blocks; rely on standardized tables and summary lines
    output_summary: str | None = None

    # Combine linting and formatting issues for the formatters
    all_issues = lint_issues + format_issues

    return ToolResult(
        name=tool.definition.name,
        success=success,
        output=output_summary,
        issues_count=issues_count,
        issues=all_issues,
    )
