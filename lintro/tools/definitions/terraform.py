"""Terraform tool definition.

Terraform is HashiCorp's infrastructure-as-code tool. Lintro wraps two of its
subcommands:

- ``terraform fmt`` — a formatter that rewrites ``*.tf``/``*.tfvars`` files to
  the canonical style. Lintro runs it in ``-check`` mode for ``check`` and
  applies it in-place for ``fix``.
- ``terraform validate`` — a check-only linter that validates the
  configuration of each module directory. Because ``validate`` requires an
  initialized working directory, Lintro first runs
  ``terraform init -backend=false -input=false`` per module directory, then
  parses ``terraform validate -json`` diagnostics.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.terraform.terraform_issue import TerraformIssue
from lintro.parsers.terraform.terraform_parser import (
    parse_terraform_fmt_output,
    parse_terraform_validate_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import filter_none_options, validate_bool

# Constants for Terraform configuration
TERRAFORM_DEFAULT_TIMEOUT: int = 60
TERRAFORM_DEFAULT_PRIORITY: int = 50
TERRAFORM_FILE_PATTERNS: list[str] = ["*.tf", "*.tfvars"]


@register_tool
@dataclass
class TerraformPlugin(BaseToolPlugin):
    """Terraform formatter and validator plugin.

    Integrates ``terraform fmt`` (formatting) and ``terraform validate``
    (configuration validation) with Lintro. Formatting issues are fixable;
    validation diagnostics are check-only.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="terraform",
            description=(
                "Terraform formatter (terraform fmt) and configuration "
                "validator (terraform validate)"
            ),
            can_fix=True,
            tool_type=(ToolType.FORMATTER | ToolType.LINTER | ToolType.INFRASTRUCTURE),
            file_patterns=TERRAFORM_FILE_PATTERNS,
            priority=TERRAFORM_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["terraform", "version"],
            min_version=get_min_version(ToolName.TERRAFORM),
            default_options={
                "timeout": TERRAFORM_DEFAULT_TIMEOUT,
                "validate": True,
            },
            default_timeout=TERRAFORM_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        validate: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Terraform-specific options with validation.

        Args:
            validate: Whether to run ``terraform validate`` in addition to
                ``terraform fmt``. When False, only formatting is checked
                (skips the ``terraform init`` step). Defaults to True.
            **kwargs: Additional base options.
        """
        validate_bool(validate, "validate")

        options = filter_none_options(validate=validate)
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return Terraform documentation URL.

        Args:
            code: Terraform issue code (``fmt``, ``validate``, ``init``).

        Returns:
            URL to the Terraform documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.TERRAFORM

    def _module_dirs(self, rel_files: list[str]) -> list[str]:
        """Compute the unique module directories to validate.

        A Terraform module is a directory containing ``*.tf`` files. Files
        inside a ``.terraform`` provider cache are ignored.

        Args:
            rel_files: File paths relative to the working directory.

        Returns:
            Sorted list of unique directory paths (``.`` for the root).
        """
        seen: set[str] = set()
        for rel_file in rel_files:
            if not rel_file.endswith(".tf"):
                continue
            if ".terraform" in rel_file.split(os.sep):
                continue
            module_dir = os.path.dirname(rel_file) or "."
            seen.add(module_dir)
        return sorted(seen)

    @staticmethod
    def _error_summary(output: str) -> str:
        """Extract a concise, single-line error summary from tool output.

        Args:
            output: Raw combined output from a failing Terraform command.

        Returns:
            The first line mentioning an error, else the first non-empty line,
            else a generic fallback message.
        """
        cleaned = strip_ansi_codes(output or "")
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        for line in lines:
            if "error" in line.lower():
                return line
        return lines[0] if lines else "command failed"

    def _timeout_issue(self, target: str, timeout: int) -> TerraformIssue:
        """Build a timeout issue for a Terraform invocation.

        Args:
            target: File or directory the timed-out command was operating on.
            timeout: The timeout value that was exceeded.

        Returns:
            A TerraformIssue describing the timeout.
        """
        return TerraformIssue(
            file=target,
            line=0,
            column=0,
            level="error",
            code="timeout",
            message=f"terraform timed out ({timeout}s limit exceeded)",
        )

    def _run_fmt_check(
        self,
        rel_files: list[str],
        cwd: str | None,
        timeout: int,
    ) -> tuple[list[TerraformIssue], str]:
        """Run ``terraform fmt -check`` over the discovered files.

        Args:
            rel_files: Terraform files relative to ``cwd``.
            cwd: Working directory for the command.
            timeout: Timeout in seconds.

        Returns:
            Tuple of (issues, combined output).
        """
        cmd = self._get_executable_command(tool_name="terraform") + [
            "fmt",
            "-check",
            *rel_files,
        ]
        logger.debug(f"[TerraformPlugin] fmt check: {' '.join(cmd)} (cwd={cwd})")
        try:
            success, output = self._run_subprocess(cmd=cmd, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            return [self._timeout_issue("terraform fmt", timeout)], ""

        issues = parse_terraform_fmt_output(output)
        # ``terraform fmt -check`` exits non-zero when files differ (expected)
        # and also on genuine errors (e.g. unparseable HCL). Surface the error
        # only when nothing was parsed as an offending file path.
        if not success and not issues and output.strip():
            issues = [
                TerraformIssue(
                    file="terraform fmt",
                    line=0,
                    column=0,
                    level="error",
                    code="fmt",
                    message=self._error_summary(output),
                ),
            ]
        return issues, output

    def _run_validate(
        self,
        rel_files: list[str],
        cwd: str | None,
        timeout: int,
    ) -> list[TerraformIssue]:
        """Validate each module directory with ``terraform validate``.

        Runs ``terraform init -backend=false -input=false`` followed by
        ``terraform validate -json`` per module directory and maps the JSON
        diagnostics to issues.

        Args:
            rel_files: Terraform files relative to ``cwd``.
            cwd: Working directory that ``rel_files`` are relative to.
            timeout: Timeout in seconds for each subprocess.

        Returns:
            List of validation issues across all module directories.
        """
        issues: list[TerraformIssue] = []

        for module_dir in self._module_dirs(rel_files):
            abs_dir = os.path.join(cwd, module_dir) if cwd else module_dir

            init_cmd = self._get_executable_command(tool_name="terraform") + [
                "init",
                "-backend=false",
                "-input=false",
                "-no-color",
            ]
            logger.debug(
                f"[TerraformPlugin] init: {' '.join(init_cmd)} (cwd={abs_dir})",
            )
            try:
                init_ok, init_output = self._run_subprocess(
                    cmd=init_cmd,
                    timeout=timeout,
                    cwd=abs_dir,
                )
            except subprocess.TimeoutExpired:
                issues.append(self._timeout_issue(module_dir, timeout))
                continue

            if not init_ok:
                init_summary = self._error_summary(init_output)
                issues.append(
                    TerraformIssue(
                        file=module_dir,
                        line=0,
                        column=0,
                        level="error",
                        code="init",
                        message=f"terraform init failed: {init_summary}",
                    ),
                )
                continue

            validate_cmd = self._get_executable_command(tool_name="terraform") + [
                "validate",
                "-json",
                "-no-color",
            ]
            logger.debug(
                f"[TerraformPlugin] validate: {' '.join(validate_cmd)} (cwd={abs_dir})",
            )
            try:
                result = self._run_subprocess_result(
                    cmd=validate_cmd,
                    timeout=timeout,
                    cwd=abs_dir,
                )
            except subprocess.TimeoutExpired:
                issues.append(self._timeout_issue(module_dir, timeout))
                continue

            parsed = parse_terraform_validate_output(result.stdout, module_dir)
            if parsed:
                issues.extend(parsed)
            elif not result.success and not result.stdout.strip():
                issues.append(
                    TerraformIssue(
                        file=module_dir,
                        line=0,
                        column=0,
                        level="error",
                        code="validate",
                        message=(
                            f"terraform validate failed: "
                            f"{self._error_summary(result.stderr or result.output)}"
                        ),
                    ),
                )

        return issues

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Terraform files with ``fmt`` and (optionally) ``validate``.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        all_issues: list[TerraformIssue] = []
        outputs: list[str] = []

        fmt_issues, fmt_output = self._run_fmt_check(
            ctx.rel_files,
            ctx.cwd,
            ctx.timeout,
        )
        all_issues.extend(fmt_issues)
        if fmt_output and fmt_output.strip():
            outputs.append(fmt_output.strip())

        if self.options.get("validate", True):
            all_issues.extend(
                self._run_validate(ctx.rel_files, ctx.cwd, ctx.timeout),
            )

        count = len(all_issues)
        output = "\n".join(outputs) if outputs else None

        return ToolResult(
            name=self.definition.name,
            success=count == 0,
            output=output if count > 0 else None,
            issues_count=count,
            issues=all_issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format Terraform files with ``terraform fmt``.

        Formatting issues are auto-fixed. Validation diagnostics (when
        ``validate`` is enabled) are reported as remaining issues because
        ``terraform validate`` cannot auto-fix.

        Args:
            paths: List of file or directory paths to format.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        ctx = self._prepare_execution(
            paths,
            options,
            no_files_message="No Terraform files to format.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        initial_fmt_issues, _ = self._run_fmt_check(
            ctx.rel_files,
            ctx.cwd,
            ctx.timeout,
        )

        # Validation is unaffected by formatting, so run it once and reuse the
        # same list for both the initial and remaining counts. It is not
        # fixable, so it persists across the fix and cancels out of the
        # fixed-count arithmetic.
        validate_issues: list[TerraformIssue] = []
        if self.options.get("validate", True):
            validate_issues = self._run_validate(
                ctx.rel_files,
                ctx.cwd,
                ctx.timeout,
            )

        fix_cmd = self._get_executable_command(tool_name="terraform") + [
            "fmt",
            *ctx.rel_files,
        ]
        logger.debug(f"[TerraformPlugin] fix: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            self._run_subprocess(cmd=fix_cmd, timeout=ctx.timeout, cwd=ctx.cwd)
        except subprocess.TimeoutExpired:
            timeout_issue = self._timeout_issue("terraform fmt", ctx.timeout)
            initial_issues = initial_fmt_issues + validate_issues
            remaining = initial_issues + [timeout_issue]
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"terraform fmt timed out ({ctx.timeout}s limit exceeded)",
                issues_count=len(remaining),
                issues=remaining,
                initial_issues=initial_issues or None,
                initial_issues_count=len(initial_issues),
                fixed_issues_count=0,
                remaining_issues_count=len(remaining),
                cwd=ctx.cwd,
            )

        remaining_fmt_issues, _ = self._run_fmt_check(
            ctx.rel_files,
            ctx.cwd,
            ctx.timeout,
        )

        initial_issues = initial_fmt_issues + validate_issues
        remaining_issues = remaining_fmt_issues + validate_issues
        initial_count = len(initial_issues)
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        summary_parts: list[str] = []
        if fixed_count > 0:
            summary_parts.append(f"Fixed {fixed_count} formatting issue(s)")
        if remaining_count > 0:
            summary_parts.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        summary = "\n".join(summary_parts) if summary_parts else "No fixes applied."

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=summary,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues=initial_issues or None,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            cwd=ctx.cwd,
        )
