"""Taplo tool definition.

Taplo is a TOML toolkit with linting and formatting capabilities.
It validates TOML syntax and can format TOML files consistently.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.taplo.taplo_issue import TaploIssue
from lintro.parsers.taplo.taplo_parser import parse_taplo_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_str,
)

# Constants for Taplo configuration
TAPLO_DEFAULT_TIMEOUT: int = 30
TAPLO_DEFAULT_PRIORITY: int = 50
TAPLO_FILE_PATTERNS: list[str] = ["*.toml"]


@register_tool
@dataclass
class TaploPlugin(BaseToolPlugin):
    """Taplo TOML linter and formatter plugin.

    This plugin integrates Taplo with Lintro for linting and formatting
    TOML files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="taplo",
            description="TOML toolkit with linting and formatting capabilities",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=TAPLO_FILE_PATTERNS,
            priority=TAPLO_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["taplo.toml", ".taplo.toml"],
            version_command=["taplo", "--version"],
            min_version=get_min_version(ToolName.TAPLO),
            default_options={
                "timeout": TAPLO_DEFAULT_TIMEOUT,
                "schema": None,
                "aligned_arrays": None,
                "aligned_entries": None,
                "array_trailing_comma": None,
                "indent_string": None,
                "reorder_keys": None,
            },
            default_timeout=TAPLO_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        schema: str | None = None,
        aligned_arrays: bool | None = None,
        aligned_entries: bool | None = None,
        array_trailing_comma: bool | None = None,
        indent_string: str | None = None,
        reorder_keys: bool | None = None,
        **kwargs: object,
    ) -> None:
        """Set Taplo-specific options with validation.

        Args:
            schema: Path or URL to JSON schema for validation.
            aligned_arrays: Align array entries.
            aligned_entries: Align table entries.
            array_trailing_comma: Add trailing comma in arrays.
            indent_string: Indentation string (default: 2 spaces).
            reorder_keys: Reorder keys alphabetically.
            **kwargs: Additional base options.
        """
        validate_str(schema, "schema")
        validate_bool(aligned_arrays, "aligned_arrays")
        validate_bool(aligned_entries, "aligned_entries")
        validate_bool(array_trailing_comma, "array_trailing_comma")
        validate_str(indent_string, "indent_string")
        validate_bool(reorder_keys, "reorder_keys")

        options = filter_none_options(
            schema=schema,
            aligned_arrays=aligned_arrays,
            aligned_entries=aligned_entries,
            array_trailing_comma=array_trailing_comma,
            indent_string=indent_string,
            reorder_keys=reorder_keys,
        )
        super().set_options(**options, **kwargs)

    def _build_format_args(self) -> list[str]:
        """Build formatting CLI arguments for Taplo.

        Returns:
            CLI arguments for Taplo formatting options.
        """
        args: list[str] = []

        if self.options.get("aligned_arrays"):
            args.append("--option=aligned_arrays=true")
        if self.options.get("aligned_entries"):
            args.append("--option=aligned_entries=true")
        if self.options.get("array_trailing_comma"):
            args.append("--option=array_trailing_comma=true")
        if self.options.get("indent_string"):
            args.append(f"--option=indent_string={self.options['indent_string']}")
        if self.options.get("reorder_keys"):
            args.append("--option=reorder_keys=true")

        return args

    def _build_lint_args(self) -> list[str]:
        """Build linting CLI arguments for Taplo.

        Returns:
            CLI arguments for Taplo linting options.
        """
        args: list[str] = []

        if self.options.get("schema"):
            args.extend(["--schema", str(self.options["schema"])])

        return args

    def _handle_timeout_error(
        self,
        timeout_val: int,
        initial_count: int | None = None,
        initial_issues: list[TaploIssue] | None = None,
    ) -> ToolResult:
        """Handle timeout errors consistently.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_count: Optional initial issues count for fix operations.
            initial_issues: Optional list of initial issues found before timeout.

        Returns:
            Standardized timeout error result.
        """
        timeout_msg = (
            f"Taplo execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options taplo:timeout=N"
        )
        timeout_issue = TaploIssue(
            file="execution",
            line=0,
            column=0,
            level="error",
            code="TIMEOUT",
            message=f"Taplo execution timed out ({timeout_val}s limit exceeded)",
        )
        if initial_count is not None and initial_count > 0:
            combined_issues = (initial_issues or []) + [timeout_issue]
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=len(combined_issues),
                issues=combined_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
            )
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=1,
            issues=[timeout_issue],
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check TOML files using Taplo.

        Runs both `taplo lint` for syntax errors and `taplo fmt --check`
        for formatting issues.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        all_issues: list[TaploIssue] = []
        all_outputs: list[str] = []
        all_success: bool = True

        # Run taplo lint for syntax errors
        lint_cmd: list[str] = self._get_executable_command(tool_name="taplo") + ["lint"]
        lint_cmd.extend(self._build_lint_args())
        lint_cmd.extend(ctx.rel_files)

        logger.debug(
            f"[TaploPlugin] Running lint: {' '.join(lint_cmd)} (cwd={ctx.cwd})",
        )
        try:
            lint_success, lint_output = self._run_subprocess(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout)

        if not lint_success:
            all_success = False
        if lint_output:
            all_outputs.append(lint_output)
            lint_issues = parse_taplo_output(output=lint_output)
            all_issues.extend(lint_issues)

        # Run taplo fmt --check for formatting issues
        fmt_cmd: list[str] = self._get_executable_command(tool_name="taplo") + [
            "fmt",
            "--check",
        ]
        fmt_cmd.extend(self._build_format_args())
        fmt_cmd.extend(ctx.rel_files)

        logger.debug(
            f"[TaploPlugin] Running format check: {' '.join(fmt_cmd)} (cwd={ctx.cwd})",
        )
        try:
            fmt_success, fmt_output = self._run_subprocess(
                cmd=fmt_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout)

        if not fmt_success:
            all_success = False
        if fmt_output:
            all_outputs.append(fmt_output)
            # Format check output may contain file paths of files that need formatting
            fmt_issues = parse_taplo_output(output=fmt_output)
            all_issues.extend(fmt_issues)

        count = len(all_issues)
        output = "\n".join(all_outputs) if all_outputs else None

        return ToolResult(
            name=self.definition.name,
            success=(all_success and count == 0),
            output=output if count > 0 else None,
            issues_count=count,
            issues=all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format TOML files using Taplo.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No TOML files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Build check command for before/after comparison
        check_cmd: list[str] = self._get_executable_command(tool_name="taplo") + [
            "fmt",
            "--check",
        ]
        check_cmd.extend(self._build_format_args())
        check_cmd.extend(ctx.rel_files)

        # Count initial formatting issues
        try:
            _, initial_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(timeout_val=ctx.timeout, initial_count=0)

        initial_issues = parse_taplo_output(output=initial_output)
        initial_count = len(initial_issues)

        # Also check for lint errors (syntax issues that formatting won't fix)
        lint_cmd: list[str] = self._get_executable_command(tool_name="taplo") + ["lint"]
        lint_cmd.extend(self._build_lint_args())
        lint_cmd.extend(ctx.rel_files)

        try:
            _, lint_output = self._run_subprocess(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
            )

        lint_issues = parse_taplo_output(output=lint_output)
        initial_count += len(lint_issues)

        # Apply formatting with taplo fmt
        fix_cmd: list[str] = self._get_executable_command(tool_name="taplo") + ["fmt"]
        fix_cmd.extend(self._build_format_args())
        fix_cmd.extend(ctx.rel_files)

        logger.debug(f"[TaploPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            _, _ = self._run_subprocess(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
            )

        # Check for remaining formatting issues
        try:
            final_success, final_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
            )

        remaining_format_issues = parse_taplo_output(output=final_output)

        # Re-check lint errors (these won't be fixed by formatting)
        try:
            _, final_lint_output = self._run_subprocess(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
            )

        remaining_lint_issues = parse_taplo_output(output=final_lint_output)

        all_remaining_issues = remaining_format_issues + remaining_lint_issues
        remaining_count = len(all_remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        # Build summary
        summary: list[str] = []
        if fixed_count > 0:
            summary.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        elif remaining_count == 0 and fixed_count > 0:
            summary.append("All issues were successfully auto-fixed")
        final_summary = "\n".join(summary) if summary else "No fixes applied."

        return ToolResult(
            name=self.definition.name,
            success=(remaining_count == 0),
            output=final_summary,
            issues_count=remaining_count,
            issues=all_remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
