"""djLint tool definition.

djLint is a linter and formatter for HTML templates written in template
languages such as Jinja, Django, Handlebars, Nunjucks, and Go templates. In
Lintro it runs in formatting mode: ``--check`` detects reformat diffs and
``--reformat`` applies them, following the same check -> fix -> verify loop as
the SQLFluff plugin so auto-fix metrics stay accurate.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.djlint.djlint_parser import parse_djlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileFixResult, FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_int,
    validate_str,
)

# Constants for djLint configuration
DJLINT_DEFAULT_TIMEOUT: int = 60
DJLINT_DEFAULT_PRIORITY: int = 50
DJLINT_DEFAULT_PROFILE: str = "jinja"
DJLINT_FILE_PATTERNS: list[str] = [
    "*.jinja",
    "*.jinja2",
    "*.j2",
    "*.twig",
    "*.nj",
]


@register_tool
@dataclass
class DjlintPlugin(BaseToolPlugin):
    """djLint HTML template linter and formatter plugin.

    This plugin integrates djLint with Lintro for checking and reformatting
    HTML template files (Jinja, Django, Handlebars, Nunjucks, Go templates).
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="djlint",
            description="HTML template linter and formatter for Jinja/Django",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=DJLINT_FILE_PATTERNS,
            priority=DJLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml", ".djlintrc"],
            version_command=["djlint", "--version"],
            min_version=get_min_version(ToolName.DJLINT),
            default_options={
                "timeout": DJLINT_DEFAULT_TIMEOUT,
                "profile": DJLINT_DEFAULT_PROFILE,
                "indent": None,
                "max_line_length": None,
                "ignore": None,
                "extend_exclude": None,
            },
            default_timeout=DJLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        profile: str | None = None,
        indent: int | None = None,
        max_line_length: int | None = None,
        ignore: str | None = None,
        extend_exclude: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set djLint-specific options.

        Args:
            profile: Template language profile (jinja, django, handlebars,
                nunjucks, golang).
            indent: Number of spaces used for indentation.
            max_line_length: Maximum line length before wrapping.
            ignore: Comma-separated rule codes to ignore (e.g. "H014,H017").
            extend_exclude: Additional comma-separated paths to exclude.
            **kwargs: Other tool options.
        """
        validate_str(profile, "profile")
        validate_int(indent, "indent", min_value=0)
        validate_int(max_line_length, "max_line_length", min_value=0)
        validate_str(ignore, "ignore")
        validate_str(extend_exclude, "extend_exclude")

        options = filter_none_options(
            profile=profile,
            indent=indent,
            max_line_length=max_line_length,
            ignore=ignore,
            extend_exclude=extend_exclude,
        )
        super().set_options(**options, **kwargs)

    def _build_common_options(self) -> list[str]:
        """Build the shared djLint option flags from current options.

        Returns:
            List of command-line option arguments common to check and fix.
        """
        cmd: list[str] = []

        profile_opt = self.options.get("profile", DJLINT_DEFAULT_PROFILE)
        if profile_opt is not None:
            cmd.extend(["--profile", str(profile_opt)])

        indent_opt = self.options.get("indent")
        if indent_opt is not None:
            cmd.extend(["--indent", str(indent_opt)])

        max_line_length_opt = self.options.get("max_line_length")
        if max_line_length_opt is not None:
            cmd.extend(["--max-line-length", str(max_line_length_opt)])

        ignore_opt = self.options.get("ignore")
        if ignore_opt is not None:
            cmd.extend(["--ignore", str(ignore_opt)])

        extend_exclude_opt = self.options.get("extend_exclude")
        if extend_exclude_opt is not None:
            cmd.extend(["--extend-exclude", str(extend_exclude_opt)])

        return cmd

    def _build_lint_command(self, files: list[str]) -> list[str]:
        """Build the djLint check command.

        Args:
            files: List of files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["djlint", *self._build_common_options(), "--check"]
        cmd.extend(files)
        return cmd

    def _build_fix_command(self, files: list[str]) -> list[str]:
        """Build the djLint reformat command.

        Args:
            files: List of files to reformat.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["djlint", *self._build_common_options(), "--reformat"]
        cmd.extend(files)
        return cmd

    def _process_single_file_check(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Process a single template file with djLint check.

        Args:
            file_path: Path to the template file to process.
            timeout: Timeout in seconds for the djLint command.

        Returns:
            FileProcessingResult with check results for this file.
        """
        cmd = self._build_lint_command(files=[str(file_path)])
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_djlint_output(output=output)
            final_success = success and len(issues) == 0
            return FileProcessingResult(
                success=final_success,
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
        """Process a single template file with djLint reformat.

        Runs check -> fix -> verify to track initial and remaining issues.

        Args:
            file_path: Path to the template file to fix.
            timeout: Timeout in seconds for the djLint command.

        Returns:
            FileFixResult with per-file processing result and fix metrics.
        """
        lint_cmd = self._build_lint_command(files=[str(file_path)])
        try:
            _check_success, check_output = self._run_subprocess(
                cmd=lint_cmd,
                timeout=timeout,
            )
            check_issues = parse_djlint_output(output=check_output)
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

        if not check_issues:
            # A non-zero exit with no parsed issues means the check invocation
            # itself failed (config error, crash, or unrecognized output), not
            # that the file is clean. Fail loudly instead of reporting success.
            if not _check_success:
                return FileFixResult(
                    file_result=FileProcessingResult(
                        success=False,
                        output=check_output,
                        issues=[],
                        error="djlint check failed before fixing",
                    ),
                    initial_count=0,
                    fixed_count=0,
                    initial_issues=[],
                )
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

        # Apply reformat. djLint exits non-zero when it changes a file, so the
        # command's exit status is not a reliable success signal; the verify
        # pass below determines what actually remains.
        fix_cmd = self._build_fix_command(files=[str(file_path)])
        try:
            _fix_success, fix_output = self._run_subprocess(
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

        # Verify by re-running the check to get the true remaining issues.
        try:
            verify_success, verify_output = self._run_subprocess(
                cmd=lint_cmd,
                timeout=timeout,
            )
            remaining_issues = parse_djlint_output(output=verify_output)
        except (subprocess.TimeoutExpired, OSError, ValueError, RuntimeError) as e:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output=fix_output,
                    issues=check_issues,
                    error=str(e),
                ),
                initial_count=len(check_issues),
                fixed_count=0,
                initial_issues=check_issues,
            )

        # Non-zero verify exit with no parsed issues means the verify check
        # invocation itself failed, not that issues remain.
        if not verify_success and not remaining_issues:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output=verify_output,
                    issues=check_issues,
                    error="djlint check failed during verification",
                ),
                initial_count=len(check_issues),
                fixed_count=0,
                initial_issues=check_issues,
            )

        fixed_count = max(0, len(check_issues) - len(remaining_issues))
        overall_success = len(remaining_issues) == 0
        output_text = "" if overall_success else fix_output

        return FileFixResult(
            file_result=FileProcessingResult(
                success=overall_success,
                output=output_text,
                issues=remaining_issues,
            ),
            initial_count=len(check_issues),
            fixed_count=fixed_count,
            initial_issues=check_issues,
        )

    def doc_url(self, code: str) -> str | None:
        """Return djLint documentation URL for the given rule code.

        Args:
            code: djLint rule code (e.g., "H013"). Formatting diffs carry no
                code and yield no URL.

        Returns:
            URL to the djLint linter documentation, or None when no code.
        """
        if code:
            return str(DocUrlTemplate.DJLINT)
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with djLint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        def processor(file_path: str) -> FileProcessingResult:
            return self._process_single_file_check(file_path, ctx.timeout)

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=processor,
            timeout=ctx.timeout,
            label="Processing files",
        )

        return ToolResult(
            name=self.definition.name,
            success=result.all_success,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=result.total_issues,
            issues=result.all_issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix formatting issues in files with djLint.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        initial_issues_total = 0
        all_initial_issues: list[BaseIssue] = []
        all_remaining_issues: list[BaseIssue] = []

        def processor(file_path: str) -> FileProcessingResult:
            nonlocal initial_issues_total
            fix_result = self._process_single_file_fix(file_path, ctx.timeout)
            initial_issues_total += fix_result.initial_count
            all_initial_issues.extend(fix_result.initial_issues)
            all_remaining_issues.extend(fix_result.remaining_issues)
            return fix_result.file_result

        result = self._process_files_with_progress(
            files=ctx.files,
            processor=processor,
            timeout=ctx.timeout,
            label="Fixing files",
        )

        remaining_count = len(all_remaining_issues)
        fixed_count = max(0, initial_issues_total - remaining_count)

        return ToolResult(
            name=self.definition.name,
            success=result.all_success and remaining_count == 0,
            output=result.build_output(timeout=ctx.timeout),
            issues_count=remaining_count,
            issues=all_remaining_issues,
            initial_issues_count=initial_issues_total,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=all_initial_issues if all_initial_issues else None,
        )
