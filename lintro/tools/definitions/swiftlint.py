"""SwiftLint tool definition.

SwiftLint enforces Swift style and conventions with 200+ built-in rules
covering code style, potential bugs, metrics, performance, and idiomatic
Swift. It runs on ``*.swift`` files, honors a project ``.swiftlint.yml``
configuration when present, and can auto-correct many rules via ``--fix``.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.swiftlint.swiftlint_parser import parse_swiftlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileFixResult, FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)

# Constants for SwiftLint configuration
SWIFTLINT_DEFAULT_TIMEOUT: int = 60
SWIFTLINT_DEFAULT_PRIORITY: int = 50
SWIFTLINT_FILE_PATTERNS: list[str] = ["*.swift"]


@register_tool
@dataclass
class SwiftlintPlugin(BaseToolPlugin):
    """SwiftLint Swift linter plugin.

    Integrates SwiftLint with Lintro for checking and auto-correcting Swift
    source files against style and correctness rules. Runs with SwiftLint's
    defaults and honors a project ``.swiftlint.yml`` / ``.swiftlint.yaml``
    configuration when one is discoverable from the working directory.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="swiftlint",
            description=(
                "Swift style and conventions linter with auto-correct support"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=SWIFTLINT_FILE_PATTERNS,
            priority=SWIFTLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".swiftlint.yml", ".swiftlint.yaml"],
            version_command=["swiftlint", "version"],
            min_version=get_min_version(ToolName.SWIFTLINT),
            default_options={
                "timeout": SWIFTLINT_DEFAULT_TIMEOUT,
            },
            default_timeout=SWIFTLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set SwiftLint-specific options.

        Args:
            timeout: Timeout in seconds (default: 60).
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return SwiftLint documentation URL for the given rule identifier.

        Args:
            code: SwiftLint rule identifier (e.g., ``identifier_name``).

        Returns:
            URL to the SwiftLint rule documentation, or ``None`` when no code
            is provided.
        """
        if code:
            return DocUrlTemplate.SWIFTLINT.format(code=code)
        return None

    def _build_check_command(self, file_path: str) -> list[str]:
        """Build the SwiftLint check command for a single file.

        Args:
            file_path: Path to the Swift file to check.

        Returns:
            List of command arguments emitting JSON diagnostics.
        """
        return [
            *self._get_executable_command(tool_name="swiftlint"),
            "lint",
            "--reporter",
            "json",
            "--quiet",
            file_path,
        ]

    def _build_fix_command(self, file_path: str) -> list[str]:
        """Build the SwiftLint auto-correct command for a single file.

        Args:
            file_path: Path to the Swift file to fix.

        Returns:
            List of command arguments applying available auto-corrections.
        """
        return [
            *self._get_executable_command(tool_name="swiftlint"),
            "--fix",
            "--quiet",
            file_path,
        ]

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single Swift file in check mode.

        Args:
            file_path: Path to the Swift file to check.
            timeout: Timeout in seconds for the SwiftLint command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        cmd = self._build_check_command(file_path)
        try:
            # Parse stdout only: SwiftLint writes its JSON report to stdout,
            # and a stderr warning merged into the stream would corrupt
            # json.loads and turn real findings into a generic failure.
            proc = self._run_subprocess_result(cmd=cmd, timeout=timeout)
            success, output = proc.success, proc.stdout
            issues = parse_swiftlint_output(output=output)
            # SwiftLint exits non-zero when it reports violations. Treat a
            # non-zero exit with no parsed issues as a genuine failure so the
            # raw diagnostic is surfaced instead of a false "clean" result.
            if not success and not issues:
                return FileProcessingResult(
                    success=False,
                    output=output,
                    issues=[],
                )
            return FileProcessingResult(
                success=True,
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
        """Process a single Swift file in fix mode.

        Runs a check to count initial issues, applies SwiftLint's
        auto-corrections, then re-checks to determine which issues remain.

        Args:
            file_path: Path to the Swift file to fix.
            timeout: Timeout in seconds for the SwiftLint commands.

        Returns:
            FileFixResult with per-file result and fix metrics.
        """
        # Initial check to count issues before fixing.
        try:
            check_proc = self._run_subprocess_result(
                cmd=self._build_check_command(file_path),
                timeout=timeout,
            )
            initial_issues = parse_swiftlint_output(output=check_proc.stdout)
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

        # Non-zero exit with no parsed issues means the invocation itself
        # failed (e.g., a compile/config error) — surface it rather than
        # reporting a clean file.
        if not check_proc.success and not initial_issues:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output=check_proc.output,
                    issues=[],
                    error="swiftlint check failed before fix",
                ),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        if not initial_issues:
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

        # Apply auto-corrections.
        try:
            self._run_subprocess_result(
                cmd=self._build_fix_command(file_path),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=initial_issues,
                    skipped=True,
                ),
                initial_count=len(initial_issues),
                fixed_count=0,
                initial_issues=initial_issues,
            )
        except (OSError, ValueError, RuntimeError) as e:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=initial_issues,
                    error=str(e),
                ),
                initial_count=len(initial_issues),
                fixed_count=0,
                initial_issues=initial_issues,
            )

        # Re-check to determine remaining issues. A failed verification with
        # nothing parsed must not read as "all fixed": conservatively keep the
        # initial issues as remaining and surface the failure.
        try:
            after_proc = self._run_subprocess_result(
                cmd=self._build_check_command(file_path),
                timeout=timeout,
            )
            remaining_issues = parse_swiftlint_output(output=after_proc.stdout)
            if not after_proc.success and not remaining_issues:
                return FileFixResult(
                    file_result=FileProcessingResult(
                        success=False,
                        output=after_proc.output,
                        issues=initial_issues,
                    ),
                    initial_count=len(initial_issues),
                    fixed_count=0,
                    initial_issues=initial_issues,
                )
        except (subprocess.TimeoutExpired, OSError, ValueError, RuntimeError):
            # If the re-check cannot run, conservatively report all initial
            # issues as remaining to preserve the fix invariant.
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output="",
                    issues=initial_issues,
                ),
                initial_count=len(initial_issues),
                fixed_count=0,
                initial_issues=initial_issues,
            )

        fixed_count = max(0, len(initial_issues) - len(remaining_issues))
        return FileFixResult(
            file_result=FileProcessingResult(
                success=len(remaining_issues) == 0,
                output="",
                issues=remaining_issues,
            ),
            initial_count=len(initial_issues),
            fixed_count=fixed_count,
            initial_issues=initial_issues,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Swift files with SwiftLint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Swift files found to check.",
        )
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
        """Auto-correct Swift files with SwiftLint, then report remaining issues.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Swift files found to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        initial_issues_total = 0
        fixed_issues_total = 0
        fixed_files: list[str] = []
        all_initial_issues: list[BaseIssue] = []
        all_remaining_issues: list[BaseIssue] = []

        def process_fix(file_path: str) -> FileProcessingResult:
            """Process a single file for fixing and accumulate metrics.

            Args:
                file_path: Path to the file to process.

            Returns:
                FileProcessingResult with processing outcome.
            """
            nonlocal initial_issues_total, fixed_issues_total
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
            label="Fixing files",
        )

        remaining_issues = initial_issues_total - fixed_issues_total

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
        per_file_output = result.build_output(timeout=ctx.timeout) or ""
        if per_file_output.strip():
            final_output = f"{summary}\n\n{per_file_output}".rstrip()
        else:
            final_output = summary

        logger.debug(
            f"[SwiftlintPlugin] Fix complete: initial={initial_issues_total}, "
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
