"""Black tool definition.

Black is an opinionated Python code formatter. It enforces a consistent style
by parsing Python code and re-printing it with its own rules, ensuring uniformity
across projects.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.black.black_issue import BlackIssue
from lintro.parsers.black.black_parser import parse_black_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_int,
    validate_str,
)

# Constants for Black configuration
BLACK_DEFAULT_TIMEOUT: int = 30
BLACK_DEFAULT_PRIORITY: int = 90  # Prefer Black ahead of Ruff formatting
BLACK_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]


@register_tool
@dataclass
class BlackPlugin(BaseToolPlugin):
    """Black Python formatter plugin.

    This plugin integrates Black with Lintro for formatting Python files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="black",
            description="Opinionated Python code formatter",
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=BLACK_FILE_PATTERNS,
            priority=BLACK_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml"],
            version_command=["black", "--version"],
            min_version="24.0.0",
            default_options={
                "timeout": BLACK_DEFAULT_TIMEOUT,
                "line_length": None,
                "target_version": None,
                "fast": False,
                "preview": False,
                "diff": False,
            },
            default_timeout=BLACK_DEFAULT_TIMEOUT,
        )

    def set_options(  # type: ignore[override]
        self,
        line_length: int | None = None,
        target_version: str | None = None,
        fast: bool | None = None,
        preview: bool | None = None,
        diff: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Black-specific options with validation.

        Args:
            line_length: Optional line length override.
            target_version: String per Black CLI (e.g., "py313").
            fast: Use --fast mode (skip safety checks).
            preview: Enable preview style.
            diff: Show diffs in output when formatting.
            **kwargs: Additional base options.
        """
        validate_int(line_length, "line_length")
        validate_str(target_version, "target_version")
        validate_bool(fast, "fast")
        validate_bool(preview, "preview")
        validate_bool(diff, "diff")

        options = filter_none_options(
            line_length=line_length,
            target_version=target_version,
            fast=fast,
            preview=preview,
            diff=diff,
        )
        super().set_options(**options, **kwargs)

    def _build_common_args(self) -> list[str]:
        """Build common CLI arguments for Black.

        Returns:
            CLI arguments for Black.
        """
        args: list[str] = []

        # Try Lintro config injection first
        config_args = self._build_config_args()
        if config_args:
            args.extend(config_args)
        else:
            if self.options.get("line_length"):
                args.extend(["--line-length", str(self.options["line_length"])])
            if self.options.get("target_version"):
                args.extend(["--target-version", str(self.options["target_version"])])

        if self.options.get("fast"):
            args.append("--fast")
        if self.options.get("preview"):
            args.append("--preview")
        return args

    def _check_line_length_violations(
        self,
        files: list[str],
        cwd: str | None,
    ) -> list[BlackIssue]:
        """Check for line length violations using the shared line-length checker.

        Args:
            files: List of file paths to check.
            cwd: Working directory for the check.

        Returns:
            List of line length violations converted to BlackIssue objects.
        """
        if not files:
            return []

        from lintro.tools.core.line_length_checker import check_line_length_violations

        line_length_opt = self.options.get("line_length")
        timeout_opt = self.options.get("timeout", BLACK_DEFAULT_TIMEOUT)
        line_length_val: int | None = None
        if isinstance(line_length_opt, int):
            line_length_val = line_length_opt
        elif line_length_opt is not None:
            line_length_val = int(str(line_length_opt))
        if isinstance(timeout_opt, int):
            timeout_val = timeout_opt
        elif timeout_opt is not None:
            timeout_val = int(str(timeout_opt))
        else:
            timeout_val = BLACK_DEFAULT_TIMEOUT

        violations = check_line_length_violations(
            files=files,
            cwd=cwd,
            line_length=line_length_val,
            timeout=timeout_val,
        )

        black_issues: list[BlackIssue] = []
        for violation in violations:
            message = (
                f"Line {violation.line} exceeds line length limit "
                f"({violation.message})"
            )
            black_issues.append(
                BlackIssue(
                    file=violation.file,
                    line=violation.line,
                    column=violation.column,
                    code=violation.code,
                    message=message,
                    severity="error",
                    fixable=False,
                ),
            )

        return black_issues

    def _handle_timeout_error(
        self,
        timeout_val: int,
        initial_count: int | None = None,
    ) -> ToolResult:
        """Handle timeout errors consistently.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_count: Optional initial issues count for fix operations.

        Returns:
            Standardized timeout error result.
        """
        timeout_msg = (
            f"Black execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options black:timeout=N"
        )
        if initial_count is not None:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=max(initial_count, 1),
                issues=[],
                initial_issues_count=(
                    initial_count if not self.options.get("diff") else 0
                ),
                fixed_issues_count=0,
                remaining_issues_count=max(initial_count, 1),
            )
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=1,
            issues=[],
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files using Black without applying changes.

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
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd: list[str] = self._get_executable_command(tool_name="black") + ["--check"]
        cmd.extend(self._build_common_args())
        cmd.extend(ctx.rel_files)

        logger.debug(f"[BlackPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")
        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout)

        black_issues = parse_black_output(output=output)

        # Check for line length violations that Black cannot wrap
        line_length_issues = self._check_line_length_violations(
            files=ctx.rel_files,
            cwd=ctx.cwd,
        )

        all_issues = black_issues + line_length_issues
        count = len(all_issues)

        return ToolResult(
            name=self.definition.name,
            success=(success and count == 0),
            output=None if count == 0 else output,
            issues_count=count,
            issues=all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format files using Black.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Merge runtime options
        merged_options = dict(self.options)
        merged_options.update(options)

        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Build reusable check command
        check_cmd: list[str] = self._get_executable_command(tool_name="black") + [
            "--check",
        ]
        check_cmd.extend(self._build_common_args())
        check_cmd.extend(ctx.rel_files)

        if self.options.get("diff"):
            initial_issues = []
            initial_line_length_issues = []
            initial_count = 0
        else:
            try:
                _, check_output = self._run_subprocess(
                    cmd=check_cmd,
                    timeout=ctx.timeout,
                    cwd=ctx.cwd,
                )
            except subprocess.TimeoutExpired:
                return self._handle_timeout_error(ctx.timeout, initial_count=0)
            initial_issues = parse_black_output(output=check_output)
            initial_line_length_issues = self._check_line_length_violations(
                files=ctx.rel_files,
                cwd=ctx.cwd,
            )
            initial_count = len(initial_issues) + len(initial_line_length_issues)

        # Apply formatting
        fix_cmd_base: list[str] = self._get_executable_command(tool_name="black")
        fix_cmd: list[str] = list(fix_cmd_base)
        if self.options.get("diff"):
            fix_cmd.append("--diff")
        fix_cmd.extend(self._build_common_args())
        fix_cmd.extend(ctx.rel_files)

        logger.debug(f"[BlackPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            _, fix_output = self._run_subprocess(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout, initial_count=initial_count)

        # Final check for remaining differences
        try:
            final_success, final_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout, initial_count=initial_count)
        remaining_issues = parse_black_output(output=final_output)

        # Check for line length violations that Black cannot wrap
        line_length_issues = self._check_line_length_violations(
            files=ctx.rel_files,
            cwd=ctx.cwd,
        )

        all_remaining_issues = remaining_issues + line_length_issues
        remaining_count = len(all_remaining_issues)

        fixed_issues_parsed = parse_black_output(output=fix_output)
        fixed_count = max(0, initial_count - remaining_count)

        # Build summary
        summary: list[str] = []
        if fixed_count > 0:
            summary.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        final_summary = "\n".join(summary) if summary else "No fixes applied."

        all_issues = (fixed_issues_parsed or []) + all_remaining_issues

        return ToolResult(
            name=self.definition.name,
            success=(remaining_count == 0),
            output=final_summary,
            issues_count=remaining_count,
            issues=all_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
