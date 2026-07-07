"""Buf tool definition.

Buf is a modern Protocol Buffer toolkit. Lintro uses two of its capabilities:

- ``buf lint`` — an extensive set of protobuf lint rules (naming conventions,
  package layout, RPC/enum best practices) emitted as newline-delimited JSON.
- ``buf format`` — a deterministic protobuf formatter, used both to detect
  unformatted files (``--diff --exit-code``) and to rewrite them (``--write``).

buf works with or without a ``buf.yaml``: when no module configuration is
present it lints against its ``STANDARD`` default rule set with the current
directory as the module root, so lintro can run it against bare ``.proto``
files without requiring project scaffolding.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.buf.buf_issue import BufIssue
from lintro.parsers.buf.buf_parser import (
    parse_buf_format_output,
    parse_buf_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_str,
)

# Constants for Buf configuration
BUF_DEFAULT_TIMEOUT: int = 30
BUF_DEFAULT_PRIORITY: int = 50
BUF_FILE_PATTERNS: list[str] = ["*.proto"]

# Rule ids that are not part of the lint rule catalog and therefore have no
# per-rule documentation anchor on the buf lint rules page.
_NON_RULE_CODES: frozenset[str] = frozenset({"COMPILE", "FORMAT"})


@register_tool
@dataclass
class BufPlugin(BaseToolPlugin):
    """Buf Protocol Buffer linter and formatter plugin.

    Integrates buf with Lintro for linting (``buf lint``) and formatting
    (``buf format``) of ``.proto`` files.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="buf",
            description="Protocol Buffer linter and formatter",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=BUF_FILE_PATTERNS,
            priority=BUF_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["buf.yaml", "buf.work.yaml"],
            version_command=["buf", "--version"],
            min_version=get_min_version(ToolName.BUF),
            default_options={
                "timeout": BUF_DEFAULT_TIMEOUT,
                "config": None,
                "disable_symlinks": None,
            },
            default_timeout=BUF_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        config: str | None = None,
        disable_symlinks: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set buf-specific options with validation.

        Args:
            config: Path to a ``buf.yaml`` file or inline config data.
            disable_symlinks: Do not follow symlinks when reading sources.
            **kwargs: Additional base options.
        """
        validate_str(config, "config")
        validate_bool(disable_symlinks, "disable_symlinks")

        options = filter_none_options(
            config=config,
            disable_symlinks=disable_symlinks,
        )
        super().set_options(**options, **kwargs)

    def _build_common_args(self, rel_files: list[str]) -> list[str]:
        """Build CLI arguments shared by lint and format invocations.

        buf takes a single positional input (the module root, ``.``) and
        restricts the operation to specific files via repeated ``--path``
        flags. This keeps the full module in scope (so imports resolve) while
        only reporting on the files lintro selected.

        Args:
            rel_files: File paths relative to the working directory.

        Returns:
            CLI arguments (``.`` input, optional config/symlink flags, and one
            ``--path`` pair per file).
        """
        args: list[str] = ["."]

        if self.options.get("config"):
            args.extend(["--config", str(self.options["config"])])
        if self.options.get("disable_symlinks"):
            args.append("--disable-symlinks")

        for rel_file in rel_files:
            args.extend(["--path", rel_file])

        return args

    def _handle_timeout_error(
        self,
        timeout_val: int,
        initial_count: int | None = None,
        initial_issues: list[BufIssue] | None = None,
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
            f"Buf execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large protobuf tree taking too long to process\n"
            "  - Need to increase timeout via --tool-options buf:timeout=N"
        )
        timeout_issue = BufIssue(
            file="execution",
            line=0,
            column=0,
            level="error",
            code="TIMEOUT",
            message=f"Buf execution timed out ({timeout_val}s limit exceeded)",
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

    def doc_url(self, code: str) -> str | None:
        """Return the buf documentation URL for a rule code.

        Args:
            code: buf rule identifier (e.g. ``PACKAGE_LOWER_SNAKE_CASE``).

        Returns:
            URL to the buf lint rules documentation, or None when the code is
            empty or refers to a non-rule finding (compile/format).
        """
        if not code or code in _NON_RULE_CODES:
            return None
        return DocUrlTemplate.BUF

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check ``.proto`` files using buf.

        Runs ``buf lint`` for rule violations and ``buf format --diff
        --exit-code`` for formatting problems.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        all_issues: list[BufIssue] = []
        all_outputs: list[str] = []

        buf_cmd = self._get_executable_command(tool_name="buf")
        common_args = self._build_common_args(ctx.rel_files)

        # buf lint — JSON violations on stdout.
        lint_cmd = buf_cmd + ["lint", *common_args, "--error-format", "json"]
        logger.debug(f"[BufPlugin] Running lint: {' '.join(lint_cmd)} (cwd={ctx.cwd})")
        try:
            lint_result = self._run_subprocess_result(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout)

        lint_issues = parse_buf_output(lint_result.stdout)
        all_issues.extend(lint_issues)
        if lint_result.stdout and lint_result.stdout.strip():
            all_outputs.append(lint_result.stdout)

        # buf format --diff --exit-code — unified diff of unformatted files.
        fmt_cmd = buf_cmd + ["format", *common_args, "--diff", "--exit-code"]
        logger.debug(
            f"[BufPlugin] Running format check: {' '.join(fmt_cmd)} (cwd={ctx.cwd})",
        )
        try:
            fmt_result = self._run_subprocess_result(
                cmd=fmt_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(ctx.timeout)

        fmt_issues = parse_buf_format_output(fmt_result.stdout)
        all_issues.extend(fmt_issues)
        if fmt_result.stdout and fmt_result.stdout.strip():
            all_outputs.append(fmt_result.stdout)

        count = len(all_issues)
        output = "\n".join(all_outputs) if all_outputs else None

        return ToolResult(
            name=self.definition.name,
            success=(count == 0),
            output=output if count > 0 else None,
            issues_count=count,
            issues=all_issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format ``.proto`` files using ``buf format --write``.

        Lint violations are not auto-fixable and are reported as remaining
        issues; only formatting problems are resolved by this method.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No .proto files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        buf_cmd = self._get_executable_command(tool_name="buf")
        common_args = self._build_common_args(ctx.rel_files)

        fmt_check_cmd = buf_cmd + ["format", *common_args, "--diff", "--exit-code"]
        lint_cmd = buf_cmd + ["lint", *common_args, "--error-format", "json"]

        # Count initial formatting + lint issues (before writing).
        try:
            initial_fmt = self._run_subprocess_result(
                cmd=fmt_check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(timeout_val=ctx.timeout, initial_count=0)

        initial_issues = parse_buf_format_output(initial_fmt.stdout)

        try:
            initial_lint = self._run_subprocess_result(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=len(initial_issues),
                initial_issues=initial_issues,
            )

        initial_issues.extend(parse_buf_output(initial_lint.stdout))
        initial_count = len(initial_issues)

        # Apply formatting in-place.
        fix_cmd = buf_cmd + ["format", *common_args, "--write"]
        logger.debug(f"[BufPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            self._run_subprocess_result(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
                initial_issues=initial_issues,
            )

        # Re-check remaining formatting + lint issues.
        try:
            final_fmt = self._run_subprocess_result(
                cmd=fmt_check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
                initial_issues=initial_issues,
            )

        remaining_issues = parse_buf_format_output(final_fmt.stdout)

        try:
            final_lint = self._run_subprocess_result(
                cmd=lint_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._handle_timeout_error(
                timeout_val=ctx.timeout,
                initial_count=initial_count,
                initial_issues=initial_issues,
            )

        remaining_issues.extend(parse_buf_output(final_lint.stdout))
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        summary: list[str] = []
        if fixed_count > 0:
            summary.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        elif fixed_count > 0:
            summary.append("All issues were successfully auto-fixed")
        final_summary = "\n".join(summary) if summary else "No fixes applied."

        return ToolResult(
            name=self.definition.name,
            success=(remaining_count == 0),
            output=final_summary,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues=initial_issues if initial_issues else None,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
