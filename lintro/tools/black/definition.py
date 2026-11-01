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

from lintro.enums.capability import Cap
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.black.black_issue import BlackIssue
from lintro.parsers.black.black_parser import (
    black_inspected_paths,
    black_report_parse_failures,
    parse_black_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.line_length_checker import (
    LineLengthCheckUnavailableError,
    RuffNotFoundError,
    check_line_length_violations,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_int,
    validate_str,
)

# Constants for Black configuration
BLACK_DEFAULT_TIMEOUT: int = 30
BLACK_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]


@dataclass
class _LineLengthResult:
    """E501 findings or an explicit reason the supplement was not measured."""

    issues: list[BlackIssue]
    note: str = ""
    failed: bool = False
    timed_out: bool = False


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
            claims=[
                Claim(
                    patterns=BLACK_FILE_PATTERNS,
                    capabilities={Cap.FORMAT, Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
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

    def set_options(
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

        line_length_opt = self.options.get("line_length")
        timeout_opt = self.options.get("timeout", BLACK_DEFAULT_TIMEOUT)
        line_length_val: int | None = None
        if isinstance(line_length_opt, int):
            line_length_val = line_length_opt
        elif line_length_opt is not None:
            line_length_val = int(str(line_length_opt))
        if isinstance(timeout_opt, bool) or timeout_opt is None:
            timeout_val = BLACK_DEFAULT_TIMEOUT
        elif isinstance(timeout_opt, (int, float)):
            # ``set_options`` normalises ``timeout`` to float, so an int()
            # round-trip through str() would raise on e.g. "120.0" (#2356).
            timeout_val = int(timeout_opt)
        else:
            timeout_val = int(float(str(timeout_opt)))

        violations = check_line_length_violations(
            files=files,
            cwd=cwd,
            line_length=line_length_val,
            timeout=timeout_val,
        )

        black_issues: list[BlackIssue] = []
        for violation in violations:
            message = (
                f"Line {violation.line} exceeds line length limit ({violation.message})"
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

    def _line_length_result(
        self,
        files: list[str],
        cwd: str | None,
    ) -> _LineLengthResult:
        """Collect E501 issues while preserving execution failures and skips.

        Args:
            files: Files included in the Black run.
            cwd: Working directory for the supplement.

        Returns:
            Findings, a visible skip note, or an execution failure.
        """
        try:
            return _LineLengthResult(self._check_line_length_violations(files, cwd))
        except RuffNotFoundError as e:
            return _LineLengthResult([], note=f"E501 supplement skipped: {e}")
        except LineLengthCheckUnavailableError as e:
            return _LineLengthResult(
                [],
                note=f"E501 supplement failed: {e}",
                failed=True,
                timed_out=e.timed_out,
            )

    def _handle_timeout_error(
        self,
        timeout_val: float,
        initial_count: int | None = None,
        cwd: str | None = None,
    ) -> ToolResult:
        """Handle timeout errors consistently.

        Args:
            timeout_val: The timeout value that was exceeded.
            initial_count: Optional initial issues count for fix operations.
            cwd: Working directory for the tool result.

        Follows the shared timeout accounting model (see
        :mod:`lintro.tools.core.timeout_utils`): a timeout is an execution
        failure reported via ``timed_out=True`` and ``success=False``, never a
        lint finding, so it does not contribute to the issue counts.

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
                issues_count=0,
                issues=[],
                initial_issues_count=initial_count,
                cwd=cwd,
                timed_out=True,
            )
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=0,
            issues=[],
            cwd=cwd,
            timed_out=True,
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
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx

        cmd: list[str] = self._get_executable_command(tool_name="black") + ["--check"]
        if self.options.get("_baseline_coverage", False):
            cmd.append("--verbose")
        cmd.extend(self._build_common_args())
        cmd.extend(ctx.rel_files)

        logger.debug(f"[BlackPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")
        try:
            process = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout, cwd=ctx.cwd)

        output = process.output
        black_issues = parse_black_output(output=output)
        parse_failures = black_report_parse_failures(output, black_issues)
        execution_failed = process.returncode not in (0, 1) or (
            process.returncode == 1 and not black_issues
        )

        # Check for line length violations that Black cannot wrap
        supplement = self._line_length_result(
            files=ctx.rel_files,
            cwd=ctx.cwd,
        )

        all_issues = black_issues + supplement.issues
        count = len(all_issues)
        result_output = output if count or execution_failed or parse_failures else None
        if supplement.note:
            result_output = "\n".join(filter(None, (output.rstrip(), supplement.note)))

        return ToolResult(
            name=self.definition.name,
            inspected_files=True,
            findings_complete=not (
                execution_failed or parse_failures or supplement.failed
            ),
            execution_failed=execution_failed or supplement.failed,
            parse_failures_count=parse_failures,
            inspected_paths=(
                black_inspected_paths(output, cwd=ctx.cwd)
                if self.options.get("_baseline_coverage", False)
                and not (execution_failed or parse_failures or supplement.failed)
                else None
            ),
            # The separate E501 subprocess has its own native exclusions.
            uninspected_codes=frozenset({"E501"}),
            success=(process.success and count == 0 and not supplement.failed),
            output=result_output,
            issues_count=count,
            issues=all_issues,
            cwd=ctx.cwd,
            timed_out=supplement.timed_out,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format files using Black.

        A failed initial E501 supplement aborts before formatting. If the final
        supplement is unavailable, retain its earlier findings and credit only
        the measured reduction in Black's own formatting issues.

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
        ctx = self.prepare(
            paths,
            options,
            no_files_message="No files to format.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        # Build reusable check command
        check_cmd: list[str] = self._get_executable_command(tool_name="black") + [
            "--check",
        ]
        check_cmd.extend(self._build_common_args())
        check_cmd.extend(ctx.rel_files)

        if self.options.get("diff"):
            initial_issues = []
            initial_supplement = _LineLengthResult([])
            initial_count = 0
        else:
            try:
                _, check_output = self._run_subprocess(
                    cmd=check_cmd,
                    timeout=ctx.timeout,
                    cwd=ctx.cwd,
                )
            except subprocess.TimeoutExpired:
                return self._handle_timeout_error(
                    ctx.timeout,
                    initial_count=0,
                    cwd=ctx.cwd,
                )
            initial_supplement = self._line_length_result(
                files=ctx.rel_files,
                cwd=ctx.cwd,
            )
            # Use preview wording when the supplement aborts before any writes.
            initial_issues = parse_black_output(
                output=check_output,
                applied=not initial_supplement.failed,
            )
            initial_issues = initial_issues + initial_supplement.issues
            initial_count = len(initial_issues)
            if initial_supplement.failed:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output="\n".join(
                        filter(None, (check_output.rstrip(), initial_supplement.note)),
                    ),
                    issues_count=initial_count,
                    issues=initial_issues,
                    initial_issues_count=initial_count,
                    cwd=ctx.cwd,
                    timed_out=initial_supplement.timed_out,
                )

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
            return self._handle_timeout_error(
                ctx.timeout,
                initial_count=initial_count,
                cwd=ctx.cwd,
            )

        # Final check for remaining differences
        try:
            final_success, final_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                ctx.timeout,
                initial_count=initial_count,
                cwd=ctx.cwd,
            )
        remaining_issues = parse_black_output(output=final_output)

        # Check for line length violations that Black cannot wrap
        supplement = self._line_length_result(
            files=ctx.rel_files,
            cwd=ctx.cwd,
        )

        # A failed or skipped measurement cannot prove earlier E501 findings
        # were fixed. Carry them forward so only measured Black fixes count.
        line_length_issues = (
            initial_supplement.issues if supplement.note else supplement.issues
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
        notes = dict.fromkeys(
            note
            for note in (
                initial_supplement.note,
                supplement.note,
                (
                    "E501 findings retained from the initial check; "
                    "line and column positions may be stale after formatting."
                    if supplement.note and line_length_issues
                    else ""
                ),
            )
            if note
        )
        final_summary = "\n".join([final_summary, *notes])

        all_issues = (fixed_issues_parsed or []) + all_remaining_issues

        return ToolResult(
            name=self.definition.name,
            inspected_files=True,
            success=(remaining_count == 0 and not supplement.failed),
            output=final_summary,
            issues_count=remaining_count,
            issues=all_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues,
            cwd=ctx.cwd,
            timed_out=supplement.timed_out,
        )
