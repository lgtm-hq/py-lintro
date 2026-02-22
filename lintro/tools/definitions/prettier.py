"""Prettier tool definition.

Prettier is an opinionated code formatter for CSS, HTML, JSON, YAML, Markdown,
GraphQL, and Astro. JavaScript/TypeScript files are handled by oxfmt for better
performance. Prettier enforces a consistent code style by parsing and
re-printing code.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.prettier.prettier_issue import PrettierIssue
from lintro.parsers.prettier.prettier_parser import parse_prettier_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
)

# Constants for Prettier configuration
PRETTIER_DEFAULT_TIMEOUT: int = 30
PRETTIER_DEFAULT_PRIORITY: int = 80
# Note: JS/TS/Vue files are handled by oxfmt (faster).
# Prettier handles file types that oxfmt doesn't support.
PRETTIER_FILE_PATTERNS: list[str] = [
    "*.css",
    "*.scss",
    "*.less",
    "*.html",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.md",
    "*.graphql",
    "*.astro",
]


@register_tool
@dataclass
class PrettierPlugin(BaseToolPlugin):
    """Prettier code formatter plugin.

    This plugin integrates Prettier with Lintro for formatting CSS, HTML,
    JSON, YAML, Markdown, GraphQL, and Astro files. JS/TS files are handled by oxfmt.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="prettier",
            description=(
                "Code formatter for CSS, HTML, JSON, YAML, Markdown, GraphQL, "
                "and Astro (JS/TS handled by oxfmt for better performance)"
            ),
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=PRETTIER_FILE_PATTERNS,
            priority=PRETTIER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.js",
                ".prettierrc.yaml",
                ".prettierrc.yml",
                "prettier.config.js",
            ],
            version_command=["prettier", "--version"],
            min_version=get_min_version(ToolName.PRETTIER),
            default_options={
                "timeout": PRETTIER_DEFAULT_TIMEOUT,
                "verbose_fix_output": False,
                "line_length": None,
            },
            default_timeout=PRETTIER_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        verbose_fix_output: bool | None = None,
        line_length: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Prettier-specific options.

        Args:
            verbose_fix_output: If True, include raw Prettier output in fix().
            line_length: Print width for prettier (maps to --print-width).
            **kwargs: Other tool options.
        """
        validate_bool(verbose_fix_output, "verbose_fix_output")
        validate_positive_int(line_length, "line_length")

        options = filter_none_options(
            verbose_fix_output=verbose_fix_output,
            line_length=line_length,
        )
        super().set_options(**options, **kwargs)

    def _find_prettier_config(self, search_dir: str | None = None) -> str | None:
        """Locate prettier config file by walking up the directory tree.

        Prettier searches upward from the file's directory to find config files,
        so we do the same to match native behavior and ensure config is found
        even when cwd is a subdirectory.

        Args:
            search_dir: Directory to start searching from. If None, searches from
                current working directory.

        Returns:
            str | None: Path to config file if found, None otherwise.
        """
        config_paths = [
            ".prettierrc",
            ".prettierrc.json",
            ".prettierrc.js",
            ".prettierrc.yaml",
            ".prettierrc.yml",
            "prettier.config.js",
            "package.json",
        ]
        # Search upward from search_dir (or cwd) to find config, just like prettier
        start_dir = os.path.abspath(search_dir) if search_dir else os.getcwd()
        current_dir = start_dir

        # Walk upward from the directory to find config
        # Stop at filesystem root to avoid infinite loop
        while True:
            for config_name in config_paths:
                config_path = os.path.join(current_dir, config_name)
                if os.path.exists(config_path):
                    # For package.json, check if it contains prettier config
                    if config_name == "package.json":
                        try:
                            with open(config_path, encoding="utf-8") as f:
                                pkg_data = json.load(f)
                                if "prettier" not in pkg_data:
                                    continue
                        except (
                            json.JSONDecodeError,
                            FileNotFoundError,
                            PermissionError,
                        ):
                            # Skip invalid or unreadable package.json files
                            continue
                    logger.debug(
                        f"[PrettierPlugin] Found config file: {config_path} "
                        f"(searched from {start_dir})",
                    )
                    return config_path

            # Move up one directory
            parent_dir = os.path.dirname(current_dir)
            # Stop if we've reached the filesystem root (parent == current)
            if parent_dir == current_dir:
                break
            current_dir = parent_dir

        return None

    def _find_prettierignore(self, search_dir: str | None = None) -> str | None:
        """Locate .prettierignore file by walking up the directory tree.

        Prettier searches upward from the file's directory to find .prettierignore,
        so we do the same to match native behavior.

        Args:
            search_dir: Directory to start searching from. If None, searches from
                current working directory.

        Returns:
            str | None: Path to .prettierignore file if found, None otherwise.
        """
        ignore_filename = ".prettierignore"
        start_dir = os.path.abspath(search_dir) if search_dir else os.getcwd()
        current_dir = start_dir

        while True:
            ignore_path = os.path.join(current_dir, ignore_filename)
            if os.path.exists(ignore_path):
                logger.debug(
                    f"[PrettierPlugin] Found .prettierignore: {ignore_path} "
                    f"(searched from {start_dir})",
                )
                return ignore_path

            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir:
                break
            current_dir = parent_dir

        return None

    def _create_not_found_result(self) -> ToolResult:
        """Create a ToolResult for when Prettier is not found.

        Returns:
            ToolResult: ToolResult instance representing Prettier not found.
        """
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=(
                "Prettier not found.\n\n"
                "Please ensure prettier is installed:\n"
                "  - Run 'npm install -g prettier' or 'bun add -g prettier'\n"
                "  - Or install locally: 'npm install prettier'"
            ),
            issues_count=0,
        )

    def _create_timeout_result(
        self,
        timeout_val: int,
        initial_issues: list[PrettierIssue] | None = None,
        initial_count: int = 0,
    ) -> ToolResult:
        """Create a ToolResult for timeout scenarios.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_issues: Optional list of issues found before timeout.
            initial_count: Optional count of initial issues.

        Returns:
            ToolResult: ToolResult instance representing timeout failure.
        """
        timeout_msg = (
            f"Prettier execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options prettier:timeout=N"
        )
        timeout_issue = PrettierIssue(
            file="execution",
            line=0,
            code="TIMEOUT",
            message=timeout_msg,
            column=0,
        )
        combined_issues = (initial_issues or []) + [timeout_issue]
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=len(combined_issues),
            issues=combined_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=0,
            remaining_issues_count=len(combined_issues),
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Prettier without making changes.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        logger.debug(
            f"[PrettierPlugin] Discovered {len(ctx.files)} files matching patterns: "
            f"{self.definition.file_patterns}",
        )
        logger.debug(
            f"[PrettierPlugin] Exclude patterns applied: {self.exclude_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[PrettierPlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[PrettierPlugin] Working directory: {ctx.cwd}")

        # Resolve executable in a manner consistent with other tools
        cmd: list[str] = self._get_executable_command(tool_name="prettier") + [
            "--check",
        ]

        # Add Lintro config injection args (--no-config, --config)
        config_args = self._build_config_args()
        if config_args:
            cmd.extend(config_args)
            logger.debug("[PrettierPlugin] Using Lintro config injection")
        else:
            # Fallback: Find config and ignore files by walking up from cwd
            found_config = self._find_prettier_config(search_dir=ctx.cwd)
            if found_config:
                logger.debug(
                    f"[PrettierPlugin] Found config: {found_config} (auto-detecting)",
                )
            else:
                logger.debug(
                    "[PrettierPlugin] No prettier config file found (using defaults)",
                )
                # Apply line_length as --print-width if set and no config found
                line_length = self.options.get("line_length")
                if line_length:
                    cmd.extend(["--print-width", str(line_length)])
                    logger.debug(
                        "[PrettierPlugin] Using --print-width=%s from options",
                        line_length,
                    )
            # Find .prettierignore by walking up from cwd
            prettierignore_path = self._find_prettierignore(search_dir=ctx.cwd)
            if prettierignore_path:
                logger.debug(
                    f"[PrettierPlugin] Found .prettierignore: {prettierignore_path} "
                    "(auto-detecting)",
                )

        cmd.extend(ctx.rel_files)
        logger.debug(f"[PrettierPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        try:
            result = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as e:
            if isinstance(e, FileNotFoundError):
                return self._create_not_found_result()
            logger.error(f"Failed to run prettier: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Prettier execution failed: {e}",
                issues_count=0,
            )

        output: str = result[1]
        issues: list[PrettierIssue] = parse_prettier_output(output=output)
        issues_count: int = len(issues)
        success: bool = issues_count == 0

        # Standardize: suppress Prettier's informational output when no issues
        final_output: str | None = output
        if success:
            final_output = None

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format files with Prettier.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Result object with counts and messages.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Get Lintro config injection args (--no-config, --config)
        config_args = self._build_config_args()
        fallback_args: list[str] = []
        if not config_args:
            # Fallback: Find config and ignore files by walking up from cwd
            found_config = self._find_prettier_config(search_dir=ctx.cwd)
            if found_config:
                logger.debug(
                    f"[PrettierPlugin] Found config: {found_config} (auto-detecting)",
                )
            else:
                logger.debug(
                    "[PrettierPlugin] No prettier config file found (using defaults)",
                )
                # Apply line_length as --print-width if set and no config found
                line_length = self.options.get("line_length")
                if line_length:
                    fallback_args.extend(["--print-width", str(line_length)])
                    logger.debug(
                        "[PrettierPlugin] Using --print-width=%s from options",
                        line_length,
                    )
            prettierignore_path = self._find_prettierignore(search_dir=ctx.cwd)
            if prettierignore_path:
                logger.debug(
                    f"[PrettierPlugin] Found .prettierignore: {prettierignore_path} "
                    "(auto-detecting)",
                )

        # Check for issues first
        check_cmd: list[str] = self._get_executable_command(tool_name="prettier") + [
            "--check",
        ]
        if config_args:
            check_cmd.extend(config_args)
        elif fallback_args:
            check_cmd.extend(fallback_args)
        check_cmd.extend(ctx.rel_files)
        logger.debug(
            f"[PrettierPlugin] Checking: {' '.join(check_cmd)} (cwd={ctx.cwd})",
        )

        try:
            check_result = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as e:
            if isinstance(e, FileNotFoundError):
                return self._create_not_found_result()
            logger.error(f"Failed to run prettier: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Prettier execution failed: {e}",
                issues_count=0,
            )

        check_output: str = check_result[1]

        # Parse initial issues
        initial_issues: list[PrettierIssue] = parse_prettier_output(output=check_output)
        initial_count: int = len(initial_issues)

        # Now fix the issues
        fix_cmd: list[str] = self._get_executable_command(tool_name="prettier") + [
            "--write",
        ]
        if config_args:
            fix_cmd.extend(config_args)
        elif fallback_args:
            fix_cmd.extend(fallback_args)
        fix_cmd.extend(ctx.rel_files)
        logger.debug(f"[PrettierPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")

        try:
            fix_result = self._run_subprocess(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(
                timeout_val=ctx.timeout,
                initial_issues=initial_issues,
                initial_count=initial_count,
            )
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as e:
            if isinstance(e, FileNotFoundError):
                return self._create_not_found_result()
            logger.error(f"Failed to run prettier: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Prettier execution failed: {e}",
                issues_count=0,
            )

        fix_output: str = fix_result[1]

        # Check for remaining issues after fixing
        try:
            final_check_result = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(
                timeout_val=ctx.timeout,
                initial_issues=initial_issues,
                initial_count=initial_count,
            )
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as e:
            if isinstance(e, FileNotFoundError):
                return self._create_not_found_result()
            logger.error(f"Failed to run prettier: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Prettier execution failed: {e}",
                issues_count=0,
            )

        final_check_output: str = final_check_result[1]
        remaining_issues: list[PrettierIssue] = parse_prettier_output(
            output=final_check_output,
        )
        remaining_count: int = len(remaining_issues)

        # Calculate fixed issues
        fixed_count: int = max(0, initial_count - remaining_count)

        # Build output message
        output_lines: list[str] = []
        if fixed_count > 0:
            output_lines.append(f"Fixed {fixed_count} formatting issue(s)")

        if remaining_count > 0:
            output_lines.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
            for issue in remaining_issues[:5]:
                output_lines.append(f"  {issue.file} - {issue.message}")
            if len(remaining_issues) > 5:
                output_lines.append(f"  ... and {len(remaining_issues) - 5} more")

        elif remaining_count == 0 and fixed_count > 0:
            output_lines.append("All formatting issues were successfully auto-fixed")

        # Add verbose raw formatting output only when explicitly requested
        if (
            self.options.get("verbose_fix_output", False)
            and fix_output
            and fix_output.strip()
        ):
            output_lines.append(f"Formatting output:\n{fix_output}")

        final_output: str | None = "\n".join(output_lines) if output_lines else None

        # Success means no remaining issues
        success: bool = remaining_count == 0

        # Combine initial and remaining issues
        all_issues = (initial_issues or []) + (remaining_issues or [])

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_output,
            issues_count=remaining_count,
            issues=all_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
