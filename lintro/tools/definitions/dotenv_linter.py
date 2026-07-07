"""dotenv-linter tool definition.

dotenv-linter is a fast, Rust-based linter for ``.env`` files. It detects
duplicate keys, lowercase keys, incorrect delimiters, unordered keys, and
other common mistakes, and can automatically fix most of them.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.dotenv_linter.dotenv_linter_parser import (
    parse_dotenv_linter_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.file_processor import FileFixResult, FileProcessingResult
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
)

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue

# Constants for dotenv-linter configuration
DOTENV_LINTER_DEFAULT_TIMEOUT: int = 30
DOTENV_LINTER_DEFAULT_PRIORITY: int = 50
DOTENV_LINTER_FILE_PATTERNS: list[str] = [".env", ".env.*", "*.env"]

# Convert a CamelCase check name to snake_case for the docs deep-link.
_CAMEL_TO_SNAKE_RE: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")


@register_tool
@dataclass
class DotenvLinterPlugin(BaseToolPlugin):
    """dotenv-linter ``.env`` file linter plugin.

    Integrates dotenv-linter with Lintro for checking and auto-fixing common
    issues in ``.env`` files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="dotenv_linter",
            description=(
                "Fast linter for .env files that detects duplicate keys, "
                "lowercase keys, and formatting issues (with auto-fix)"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=DOTENV_LINTER_FILE_PATTERNS,
            priority=DOTENV_LINTER_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["dotenv-linter", "--version"],
            min_version=get_min_version(ToolName.DOTENV_LINTER),
            default_options={
                "timeout": DOTENV_LINTER_DEFAULT_TIMEOUT,
                "recursive": False,
                "exclude": None,
                "skip_checks": None,
                "schema": None,
            },
            default_timeout=DOTENV_LINTER_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        recursive: bool | None = None,
        exclude: list[str] | str | None = None,
        skip_checks: list[str] | str | None = None,
        schema: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set dotenv-linter-specific options.

        Args:
            recursive: Recursively scan directories for ``.env`` files.
            exclude: File or directory paths to exclude from linting/fixing.
            skip_checks: Check names to bypass (e.g., ["LowercaseKey"]).
                Maps to dotenv-linter's ``--ignore-checks``.
            schema: Path to a schema file to validate ``.env`` contents.
            **kwargs: Other tool options.
        """
        validate_bool(recursive, "recursive")
        exclude = normalize_str_or_list(exclude, "exclude")
        skip_checks = normalize_str_or_list(skip_checks, "skip_checks")

        options = filter_none_options(
            recursive=recursive,
            exclude=exclude,
            skip_checks=skip_checks,
            schema=schema,
        )
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return the dotenv-linter docs URL for the given check.

        Args:
            code: Check name (e.g., "LowercaseKey").

        Returns:
            URL to the check's documentation page, or None when no code.
        """
        if not code:
            return None
        slug = _CAMEL_TO_SNAKE_RE.sub("_", code).lower()
        return DocUrlTemplate.DOTENV_LINTER.format(code=slug)

    def _build_common_args(self) -> list[str]:
        """Build CLI arguments shared by the check and fix subcommands.

        Returns:
            List of common CLI arguments.
        """
        args: list[str] = ["--plain"]

        if self.options.get("recursive"):
            args.append("--recursive")

        exclude_opt = self.options.get("exclude")
        if isinstance(exclude_opt, list):
            for path in exclude_opt:
                args.extend(["--exclude", str(path)])

        skip_checks_opt = self.options.get("skip_checks")
        if isinstance(skip_checks_opt, list):
            for check in skip_checks_opt:
                args.extend(["--ignore-checks", str(check)])

        schema_opt = self.options.get("schema")
        if schema_opt is not None:
            args.extend(["--schema", str(schema_opt)])

        return args

    def _process_single_file(
        self,
        file_path: str,
        timeout: int,
    ) -> FileProcessingResult:
        """Check a single ``.env`` file with dotenv-linter.

        Args:
            file_path: Path to the ``.env`` file to check.
            timeout: Timeout in seconds for the dotenv-linter command.

        Returns:
            FileProcessingResult with processing outcome.
        """
        cmd = [
            *self._get_executable_command(tool_name="dotenv-linter"),
            "check",
            *self._build_common_args(),
            file_path,
        ]
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout)
            issues = parse_dotenv_linter_output(output=output)
            # dotenv-linter exits non-zero when it reports problems. Treat a
            # non-zero exit with no parsed issues as a genuine failure so real
            # invocation errors are not silently reported as clean.
            if not success and not issues:
                return FileProcessingResult(
                    success=False,
                    output=output,
                    issues=[],
                    error="dotenv-linter check failed",
                )
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
        """Fix a single ``.env`` file with dotenv-linter.

        Runs an initial check to capture the pre-fix issue set, applies the
        fix in place, then re-checks to determine which issues remain. This
        keeps the ToolResult invariant intact
        (``initial = fixed + remaining``) even for checks dotenv-linter cannot
        auto-fix.

        Args:
            file_path: Path to the ``.env`` file to fix.
            timeout: Timeout in seconds for each dotenv-linter command.

        Returns:
            FileFixResult with per-file processing result and fix metrics.
        """
        initial = self._process_single_file(file_path, timeout)
        if initial.skipped or initial.error:
            return FileFixResult(
                file_result=initial,
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        initial_issues: list[DotenvLinterIssue] = list(initial.issues)
        if not initial_issues:
            return FileFixResult(
                file_result=FileProcessingResult(success=True, output="", issues=[]),
                initial_count=0,
                fixed_count=0,
                initial_issues=[],
            )

        # ``--no-backup`` prevents dotenv-linter from writing ``.env.bak``
        # files alongside the fixed file.
        fix_cmd = [
            *self._get_executable_command(tool_name="dotenv-linter"),
            "fix",
            "--no-backup",
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

        if not fix_success:
            return FileFixResult(
                file_result=FileProcessingResult(
                    success=False,
                    output=fix_output,
                    issues=initial_issues,
                ),
                initial_count=len(initial_issues),
                fixed_count=0,
                initial_issues=initial_issues,
            )

        # Re-check to determine which issues remain after fixing. Issues that
        # survived an attempted fix are marked non-fixable so downstream
        # consumers do not offer an auto-fix that was already tried.
        recheck = self._process_single_file(file_path, timeout)
        remaining_issues: list[DotenvLinterIssue] = [
            replace(issue, fixable=False) for issue in recheck.issues
        ]
        fixed_count = max(len(initial_issues) - len(remaining_issues), 0)

        return FileFixResult(
            file_result=FileProcessingResult(
                success=recheck.success and not remaining_issues,
                output=recheck.output,
                issues=remaining_issues,
            ),
            initial_count=len(initial_issues),
            fixed_count=fixed_count,
            initial_issues=initial_issues,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check ``.env`` files with dotenv-linter.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
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
        """Fix issues in ``.env`` files with dotenv-linter.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths=paths,
            options=options,
            no_files_message="No files to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        initial_issues_total = 0
        fixed_issues_total = 0
        fixed_files: list[str] = []
        all_initial_issues: list[BaseIssue] = []
        all_remaining_issues: list[BaseIssue] = []

        def process_fix(file_path: str) -> FileProcessingResult:
            """Fix a single file, accumulating fix metrics.

            Args:
                file_path: Path to the file to fix.

            Returns:
                FileProcessingResult with the per-file outcome.
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

        # Count what the post-fix re-check actually reported rather than the
        # arithmetic remainder: a fix can change the issue set (new findings
        # surfacing after rewrites), and issues_count must match ``issues``.
        remaining_issues = len(all_remaining_issues)

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
