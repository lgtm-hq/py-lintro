"""Shfmt tool definition.

Shfmt is a shell script formatter that supports POSIX, Bash, and mksh shells.
It formats shell scripts to ensure consistent style and can detect formatting
issues in diff mode.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.shfmt.shfmt_parser import parse_shfmt_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileFixResult, FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_int,
    validate_str,
)

# Constants for shfmt configuration
SHFMT_DEFAULT_TIMEOUT: int = 30
SHFMT_DEFAULT_PRIORITY: int = 50
SHFMT_FILE_PATTERNS: list[str] = ["*.sh", "*.bash", "*.ksh"]


@register_tool
@dataclass
class ShfmtPlugin(BaseToolPlugin):
    """Shfmt shell script formatter plugin.

    This plugin integrates shfmt with Lintro for formatting shell scripts.
    It supports POSIX, Bash, and mksh shells with various formatting options.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="shfmt",
            description=(
                "Shell script formatter supporting POSIX, Bash, and mksh shells"
            ),
            can_fix=True,
            tool_type=ToolType.FORMATTER,
            file_patterns=SHFMT_FILE_PATTERNS,
            priority=SHFMT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".editorconfig"],
            version_command=["shfmt", "--version"],
            min_version=get_min_version(ToolName.SHFMT),
            default_options={
                "timeout": SHFMT_DEFAULT_TIMEOUT,
                "indent": None,
                "binary_next_line": False,
                "switch_case_indent": False,
                "space_redirects": False,
                "language_dialect": None,
                "simplify": False,
            },
            default_timeout=SHFMT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        indent: int | None = None,
        binary_next_line: bool | None = None,
        switch_case_indent: bool | None = None,
        space_redirects: bool | None = None,
        language_dialect: str | None = None,
        simplify: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set shfmt-specific options.

        Args:
            indent: Indentation size. 0 for tabs, >0 for that many spaces.
            binary_next_line: Binary ops like && and | may start a line.
            switch_case_indent: Indent switch cases.
            space_redirects: Redirect operators followed by space.
            language_dialect: Shell language dialect (bash, posix, mksh, bats).
            simplify: Simplify code where possible.
            **kwargs: Other tool options.

        Raises:
            ValueError: If language_dialect is not a valid dialect.
        """
        validate_int(indent, "indent")
        validate_bool(binary_next_line, "binary_next_line")
        validate_bool(switch_case_indent, "switch_case_indent")
        validate_bool(space_redirects, "space_redirects")
        validate_str(language_dialect, "language_dialect")
        validate_bool(simplify, "simplify")

        # Validate language_dialect if provided
        if language_dialect is not None:
            valid_dialects = {"bash", "posix", "mksh", "bats"}
            if language_dialect.lower() not in valid_dialects:
                msg = (
                    f"Invalid language_dialect: {language_dialect!r}. "
                    f"Must be one of: {', '.join(sorted(valid_dialects))}"
                )
                raise ValueError(msg)
            language_dialect = language_dialect.lower()

        options = filter_none_options(
            indent=indent,
            binary_next_line=binary_next_line,
            switch_case_indent=switch_case_indent,
            space_redirects=space_redirects,
            language_dialect=language_dialect,
            simplify=simplify,
        )
        super().set_options(**options, **kwargs)

    def _build_common_args(self) -> list[str]:
        """Build common CLI arguments for shfmt.

        Returns:
            CLI arguments for shfmt.
        """
        args: list[str] = []

        # Indentation
        indent = self.options.get("indent")
        if indent is not None:
            args.extend(["-i", str(indent)])

        # Binary operations at start of line
        if self.options.get("binary_next_line"):
            args.append("-bn")

        # Switch case indentation
        if self.options.get("switch_case_indent"):
            args.append("-ci")

        # Space after redirect operators
        if self.options.get("space_redirects"):
            args.append("-sr")

        # Language dialect
        language_dialect = self.options.get("language_dialect")
        if language_dialect is not None:
            args.extend(["-ln", str(language_dialect)])

        # Simplify code
        if self.options.get("simplify"):
            args.append("-s")

        return args

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single file in check mode.

        Args:
            file_path: Path to the shell script to check.
            timeout: Timeout in seconds for the shfmt command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        cmd = self._get_executable_command(tool_name="shfmt") + ["-d"]
        cmd.extend(self._build_common_args())
        cmd.append(file_path)

        try:
            success, output = self._run_subprocess(
                cmd=cmd,
                timeout=timeout,
            )
            issues = parse_shfmt_output(output=output)
            return FileProcessingResult(
                success=success,
                output=output,
                issues=issues,
            )
        except subprocess.TimeoutExpired:
            return FileProcessingResult(
                success=False,
                output="",
                issues=[],
                skipped=True,
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileProcessingResult(
                success=False,
                output="",
                issues=[],
                error=str(e),
            )

    def _process_single_file_fix(
        self,
        file_path: str,
        timeout: int,
    ) -> FileFixResult:
        """Process a single file in fix mode.

        Args:
            file_path: Path to the shell script to fix.
            timeout: Timeout in seconds for the shfmt command.

        Returns:
            FileFixResult with per-file processing result and fix metrics.
        """
        # First check if file needs formatting
        check_cmd = [
            *self._get_executable_command(tool_name="shfmt"),
            "-d",
            *self._build_common_args(),
            file_path,
        ]

        # Check step
        try:
            check_success, check_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=timeout,
            )
            check_issues = parse_shfmt_output(output=check_output)
        except subprocess.TimeoutExpired:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=[],
                    skipped=True,
                ),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=[],
                    error=str(e),
                ),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        # shfmt -d exits non-zero when a diff is produced (expected).
        # If it exits non-zero AND we parsed no diff, the invocation
        # itself failed — surface the error instead of reporting a
        # clean file.
        if not check_success and not check_issues:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output=check_output,
                    issues=[],
                    error="shfmt check failed before fix",
                ),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        if not check_issues:
            # No issues found, file is already formatted
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=True,
                    output="",
                    issues=[],
                ),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        # Apply fix with -w flag
        fix_cmd = [
            *self._get_executable_command(tool_name="shfmt"),
            "-w",
            *self._build_common_args(),
            file_path,
        ]

        try:
            fix_success, fix_output = self._run_subprocess(
                cmd=fix_cmd,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=check_issues,
                    skipped=True,
                ),
                initial_count=len(check_issues),
                fixed_count=0,
                initial_issues=check_issues,
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=check_issues,
                    error=str(e),
                ),
                initial_count=len(check_issues),
                fixed_count=0,
                initial_issues=check_issues,
            )

        if fix_success:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=True,
                    output="",
                    issues=[],
                ),
                initial_count=len(check_issues),
                fixed_count=len(check_issues),
                initial_issues=check_issues,
            )
        # Fix command exited non-zero: preserve the subprocess output
        # (stderr/stdout) so users can see why the fix failed.
        return FileFixResult(
            file_result=FileProcessingResult(
                success=False,
                output=fix_output,
                issues=check_issues,
            ),
            initial_count=len(check_issues),
            fixed_count=0,
            initial_issues=check_issues,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with shfmt.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=lambda f: self._process_single_file(f, ctx.timeout),
            timeout=ctx.timeout,
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success and result.total_issues == 0,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=result.total_issues,
            issues=result.all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix formatting issues in files with shfmt.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Track fix-specific metrics. We collect remaining issues per-file
        # because AggregatedResult drops issues from skipped/errored files,
        # which would undercount the final issues list.
        initial_issues_total = 0
        fixed_issues_total = 0
        fixed_files: list[str] = []
        all_initial_issues: list[BaseIssue] = []
        all_remaining_issues: list[BaseIssue] = []

        def process_fix(file_path: str) -> FileProcessingResult:
            """Process a single file for fixing.

            Args:
                file_path: Path to the file to process.

            Returns:
                FileProcessingResult with processing outcome.
            """
            nonlocal initial_issues_total, fixed_issues_total, fixed_files
            fix_result = self._process_single_file_fix(
                file_path=file_path,
                timeout=ctx.timeout,
            )
            initial_issues_total += fix_result.initial_count
            fixed_issues_total += fix_result.fixed_count
            all_initial_issues.extend(fix_result.initial_issues)
            all_remaining_issues.extend(fix_result.remaining_issues)
            if fix_result.fixed_count > 0:
                fixed_files.append(file_path)
            return fix_result.file_result

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=process_fix,
            timeout=ctx.timeout,
            label="Formatting files",
        )

        # Calculate remaining issues
        remaining_issues = initial_issues_total - fixed_issues_total

        # Build summary output
        summary_parts: list[str] = []
        if fixed_issues_total > 0:
            summary_parts.append(
                f"Fixed {fixed_issues_total} issue(s) in {len(fixed_files)} file(s)",
            )
        if remaining_issues > 0:
            summary_parts.append(
                f"Found {remaining_issues} issue(s) that could not be fixed",
            )
        if result.execution_failures > 0:
            summary_parts.append(
                f"Failed to process {result.execution_failures} file(s)",
            )

        summary = "\n".join(summary_parts) if summary_parts else "No fixes needed."
        # Combine the summary with per-file diagnostic output so failure
        # messages from the fix subprocess are preserved alongside the
        # high-level counts.
        per_file_output = result.build_output(timeout=ctx.timeout) or ""
        if per_file_output.strip():
            final_output = f"{summary}\n\n{per_file_output}".rstrip()
        else:
            final_output = summary

        logger.debug(
            f"[ShfmtPlugin] Fix complete: initial={initial_issues_total}, "
            f"fixed={fixed_issues_total}, remaining={remaining_issues}",
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success and remaining_issues == 0,
            output=final_output,
            issues_count=remaining_issues,
            issues=all_remaining_issues,
            initial_issues_count=initial_issues_total,
            fixed_issues_count=fixed_issues_total,
            remaining_issues_count=remaining_issues,
            initial_issues=all_initial_issues if all_initial_issues else None,
        )
