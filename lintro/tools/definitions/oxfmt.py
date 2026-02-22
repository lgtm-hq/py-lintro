"""Oxfmt tool definition.

Oxfmt is a fast JavaScript/TypeScript formatter (30x faster than Prettier).
It formats code with minimal configuration, enforcing a consistent code style.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxfmt.oxfmt_issue import OxfmtIssue
from lintro.parsers.oxfmt.oxfmt_parser import parse_oxfmt_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_list,
    validate_str,
)

# Constants for oxfmt configuration
OXFMT_DEFAULT_TIMEOUT: int = 30
OXFMT_DEFAULT_PRIORITY: int = 80
# Note: oxfmt (from oxc toolchain) supports JavaScript/TypeScript and Vue files.
# Unlike Prettier, it does not support Svelte, Astro, JSON, CSS, HTML, Markdown, etc.
OXFMT_FILE_PATTERNS: list[str] = [
    "*.js",
    "*.mjs",
    "*.cjs",
    "*.jsx",
    "*.ts",
    "*.mts",
    "*.cts",
    "*.tsx",
    "*.vue",
]


@register_tool
@dataclass
class OxfmtPlugin(BaseToolPlugin):
    """Oxfmt code formatter plugin.

    This plugin integrates oxfmt with Lintro for formatting
    JavaScript, TypeScript, and Vue files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="oxfmt",
            description=(
                "Fast JavaScript/TypeScript formatter (30x faster than Prettier)"
            ),
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=OXFMT_FILE_PATTERNS,
            priority=OXFMT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".oxfmtrc.json", ".oxfmtrc.jsonc"],
            version_command=["oxfmt", "--version"],
            min_version=get_min_version(ToolName.OXFMT),
            default_options={
                "timeout": OXFMT_DEFAULT_TIMEOUT,
                "verbose_fix_output": False,
            },
            default_timeout=OXFMT_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        exclude_patterns: list[str] | None = None,
        include_venv: bool = False,
        verbose_fix_output: bool | None = None,
        config: str | None = None,
        ignore_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set oxfmt-specific options.

        Args:
            exclude_patterns: List of patterns to exclude.
            include_venv: Whether to include virtual environment directories.
            verbose_fix_output: If True, include raw oxfmt output in fix().
            config: Path to oxfmt config file (--config).
            ignore_path: Path to ignore file (--ignore-path).
            **kwargs: Other tool options.

        Note:
            Formatting options (print_width, tab_width, use_tabs, semi, single_quote)
            are only supported via config file (.oxfmtrc.json), not CLI flags.
        """
        validate_list(exclude_patterns, "exclude_patterns")
        validate_bool(verbose_fix_output, "verbose_fix_output")
        validate_str(config, "config")
        validate_str(ignore_path, "ignore_path")

        if exclude_patterns is not None:
            self.exclude_patterns = exclude_patterns.copy()
        self.include_venv = include_venv

        options = filter_none_options(
            verbose_fix_output=verbose_fix_output,
            config=config,
            ignore_path=ignore_path,
        )
        super().set_options(**options, **kwargs)

    def _create_timeout_result(
        self,
        timeout_val: int,
        initial_issues: list[OxfmtIssue] | None = None,
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
            f"Oxfmt execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options oxfmt:timeout=N"
        )
        timeout_issue = OxfmtIssue(
            file="execution",
            line=1,
            code="TIMEOUT",
            message=timeout_msg,
            column=1,
        )
        combined_issues = (initial_issues or []) + [timeout_issue]
        combined_count = len(combined_issues)
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=combined_count,
            issues=combined_issues,
            initial_issues_count=combined_count,
            fixed_issues_count=0,
            remaining_issues_count=combined_count,
        )

    def _build_oxfmt_args(self, options: dict[str, object]) -> list[str]:
        """Build CLI arguments from options.

        Args:
            options: Options dict to build args from (use merged_options).

        Returns:
            List of CLI arguments to pass to oxfmt.

        Note:
            Formatting options (print_width, tab_width, use_tabs, semi, single_quote)
            are only supported via config file (.oxfmtrc.json), not CLI flags.
        """
        args: list[str] = []

        # Config file override
        config = options.get("config")
        if config:
            args.extend(["--config", str(config)])

        # Ignore file path
        ignore_path = options.get("ignore_path")
        if ignore_path:
            args.extend(["--ignore-path", str(ignore_path)])

        return args

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with oxfmt without making changes.

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
            assert ctx.early_result is not None
            return ctx.early_result

        logger.debug(
            f"[OxfmtPlugin] Discovered {len(ctx.files)} files matching patterns: "
            f"{self.definition.file_patterns}",
        )
        logger.debug(
            f"[OxfmtPlugin] Exclude patterns applied: {self.exclude_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[OxfmtPlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[OxfmtPlugin] Working directory: {ctx.cwd}")

        # Resolve executable in a manner consistent with other tools
        # Use --list-different to get file paths that need formatting (one per line)
        # Note: --check and --list-different are mutually exclusive in oxfmt
        cmd: list[str] = self._get_executable_command(tool_name="oxfmt") + [
            "--list-different",
        ]

        # Add Lintro config injection args if available
        config_args = self._build_config_args()
        if config_args:
            cmd.extend(config_args)
            logger.debug("[OxfmtPlugin] Using Lintro config injection")

        # Add oxfmt-specific CLI arguments from options
        oxfmt_args = self._build_oxfmt_args(merged_options)
        if oxfmt_args:
            cmd.extend(oxfmt_args)

        cmd.extend(ctx.rel_files)
        logger.debug(f"[OxfmtPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        try:
            result = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)

        output: str = result[1]
        issues: list[OxfmtIssue] = parse_oxfmt_output(output=output)
        issues_count: int = len(issues)
        success: bool = issues_count == 0

        # Standardize: suppress oxfmt's informational output when no issues
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
        """Format files with oxfmt.

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
            assert ctx.early_result is not None
            return ctx.early_result

        # Get Lintro config injection args
        config_args = self._build_config_args()

        # Add oxfmt-specific CLI arguments from options
        oxfmt_args = self._build_oxfmt_args(merged_options)

        # Check for issues first using --list-different
        # Note: --check and --list-different are mutually exclusive in oxfmt
        check_cmd: list[str] = self._get_executable_command(tool_name="oxfmt") + [
            "--list-different",
        ]
        if config_args:
            check_cmd.extend(config_args)
        if oxfmt_args:
            check_cmd.extend(oxfmt_args)
        check_cmd.extend(ctx.rel_files)
        logger.debug(
            f"[OxfmtPlugin] Checking: {' '.join(check_cmd)} (cwd={ctx.cwd})",
        )

        try:
            check_result = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout)

        check_output: str = check_result[1]

        # Parse initial issues
        initial_issues: list[OxfmtIssue] = parse_oxfmt_output(output=check_output)
        initial_count: int = len(initial_issues)

        # Now fix the issues
        fix_cmd: list[str] = self._get_executable_command(tool_name="oxfmt") + [
            "--write",
        ]
        if config_args:
            fix_cmd.extend(config_args)
        if oxfmt_args:
            fix_cmd.extend(oxfmt_args)
        fix_cmd.extend(ctx.rel_files)
        logger.debug(f"[OxfmtPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")

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

        final_check_output: str = final_check_result[1]
        remaining_issues: list[OxfmtIssue] = parse_oxfmt_output(
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
        elif fixed_count > 0:
            # remaining_count == 0 is implied by the elif
            output_lines.append("All formatting issues were successfully auto-fixed")

        # Add verbose raw formatting output only when explicitly requested
        if (
            merged_options.get("verbose_fix_output", False)
            and fix_output
            and fix_output.strip()
        ):
            output_lines.append(f"Formatting output:\n{fix_output}")

        final_output: str | None = "\n".join(output_lines) if output_lines else None

        # Success means no remaining issues
        success: bool = remaining_count == 0

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_output,
            issues_count=remaining_count,
            issues=remaining_issues or [],
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
