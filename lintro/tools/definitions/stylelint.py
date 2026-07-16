"""Stylelint tool definition.

Stylelint is a mighty, configurable linter for CSS, SCSS, Sass, and Less
stylesheets. It reports rule violations and syntax errors and can auto-fix
many of them via ``--fix``.
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
from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.parsers.stylelint.stylelint_parser import parse_stylelint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
    validate_str,
)

# Constants for Stylelint configuration
STYLELINT_DEFAULT_TIMEOUT: int = 30
STYLELINT_DEFAULT_PRIORITY: int = 50
STYLELINT_FILE_PATTERNS: list[str] = ["*.css", "*.scss", "*.sass", "*.less"]
# Rule codes that are not real stylelint rules (no documentation page).
STYLELINT_PSEUDO_RULES: frozenset[str] = frozenset(
    {"CssSyntaxError", "parseError", "invalidOption", "TIMEOUT"},
)
STYLELINT_CONFIG_FILENAMES: tuple[str, ...] = (
    ".stylelintrc",
    ".stylelintrc.json",
    ".stylelintrc.yaml",
    ".stylelintrc.yml",
    ".stylelintrc.js",
    ".stylelintrc.cjs",
    ".stylelintrc.mjs",
    "stylelint.config.js",
    "stylelint.config.cjs",
    "stylelint.config.mjs",
)


@register_tool
@dataclass
class StylelintPlugin(BaseToolPlugin):
    """Stylelint CSS/SCSS/Less linter plugin.

    This plugin integrates Stylelint with Lintro for linting and fixing
    CSS, SCSS, Sass, and Less stylesheets.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="stylelint",
            description=(
                "CSS/SCSS/Sass/Less linter and fixer with 100+ built-in rules"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=STYLELINT_FILE_PATTERNS,
            priority=STYLELINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(STYLELINT_CONFIG_FILENAMES),
            version_command=["stylelint", "--version"],
            min_version=get_min_version(ToolName.STYLELINT),
            default_options={
                "timeout": STYLELINT_DEFAULT_TIMEOUT,
                "verbose_fix_output": False,
                "config": None,
            },
            default_timeout=STYLELINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        verbose_fix_output: bool | None = None,
        config: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Stylelint-specific options.

        Args:
            verbose_fix_output: If True, include raw Stylelint output in fix().
            config: Path to a stylelint config file (maps to ``--config``).
            timeout: Timeout in seconds (default: 30).
            **kwargs: Other tool options.
        """
        validate_bool(verbose_fix_output, "verbose_fix_output")
        validate_str(config, "config")
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(
            verbose_fix_output=verbose_fix_output,
            config=config,
            timeout=timeout,
        )
        super().set_options(**options, **kwargs)

    @staticmethod
    def _is_no_config_error(output: str | None) -> bool:
        """Report whether output indicates a missing stylelint configuration.

        Stylelint requires a configuration to run and exits with a
        ``ConfigurationError`` (no JSON payload) when none is resolvable for a
        linted file. Detect that so lintro can skip gracefully rather than
        surfacing a hard failure.

        Args:
            output: Combined stdout/stderr from stylelint.

        Returns:
            True if the output signals a missing/unresolvable configuration.
        """
        if not output:
            return False
        return "No configuration provided" in output or (
            "No configuration found" in output
        )

    def _create_no_config_result(self, cwd: str | None = None) -> ToolResult:
        """Create a skip ToolResult for when no stylelint config is found.

        Stylelint cannot lint without a configuration, so lintro skips it (as
        a non-error) rather than surfacing a hard failure. This keeps runs
        clean for projects that do not use stylelint.

        Args:
            cwd: Working directory for the tool result.

        Returns:
            ToolResult: Skip result (success=True) with a helpful message.
        """
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=(
                "Skipping stylelint: no stylelint configuration found "
                "(e.g. .stylelintrc.json, stylelint.config.js, or a "
                '"stylelint" key in package.json). Add one to enable '
                "CSS/SCSS/Less linting."
            ),
            issues_count=0,
            cwd=cwd,
        )

    def _create_timeout_result(
        self,
        timeout_val: int,
        cwd: str | None = None,
    ) -> ToolResult:
        """Create a ToolResult for timeout scenarios.

        Args:
            timeout_val: The timeout value that was exceeded.
            cwd: Working directory for the tool result.

        Returns:
            ToolResult: ToolResult instance representing timeout failure.
        """
        timeout_msg = (
            f"Stylelint execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options stylelint:timeout=N"
        )
        timeout_issue = StylelintIssue(
            file="execution",
            line=1,
            column=1,
            code="TIMEOUT",
            message=timeout_msg,
            severity="error",
            fixable=False,
        )
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=timeout_msg,
            issues_count=1,
            issues=[timeout_issue],
            cwd=cwd,
        )

    def doc_url(self, code: str) -> str | None:
        """Return stylelint documentation URL for the given rule.

        Args:
            code: Stylelint rule name (e.g., ``color-hex-length``).

        Returns:
            URL to the stylelint rule documentation, or None for pseudo-rules
            (syntax/parse errors) and empty codes.
        """
        if not code or code in STYLELINT_PSEUDO_RULES:
            return None
        return DocUrlTemplate.STYLELINT.format(code=code)

    def _base_command(
        self,
        merged_options: dict[str, object] | None = None,
    ) -> list[str]:
        """Build the base stylelint command, honoring an explicit config.

        Config discovery is otherwise delegated to stylelint itself, which
        resolves configuration per linted file (walking up from each file).

        Args:
            merged_options: Per-call options merged over the instance
                defaults; falls back to ``self.options`` when omitted so a
                caller-supplied ``config`` is not silently ignored.

        Returns:
            The base command list.
        """
        cmd = [*self._get_executable_command(tool_name="stylelint")]
        opts = merged_options if merged_options is not None else self.options
        explicit = opts.get("config")
        if explicit:
            cmd.extend(["--config", str(explicit)])
        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Stylelint without making changes.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        merged_options = dict(self.options)
        merged_options.update(options)

        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = [
            *self._base_command(merged_options),
            "--formatter",
            "json",
            *ctx.rel_files,
        ]
        logger.debug(f"[StylelintPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        try:
            run_success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout, cwd=ctx.cwd)

        issues = parse_stylelint_output(output=output)
        if not issues and self._is_no_config_error(output):
            return self._create_no_config_result(cwd=ctx.cwd)
        # Stylelint exits 2 when violations exist (parsed above); any other
        # failure with nothing parsed (crash, invalid flag or config) must
        # not read as a clean run.
        if not issues and not run_success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=output or "Stylelint exited with an error and no results.",
                issues_count=0,
                cwd=ctx.cwd,
            )
        issues_count = len(issues)
        success = issues_count == 0

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=output if not success else None,
            issues_count=issues_count,
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix auto-fixable issues in files with Stylelint.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Result object with counts and messages.
        """
        merged_options = dict(self.options)
        merged_options.update(options)

        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to fix.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        base_cmd = self._base_command(merged_options)
        check_cmd = [*base_cmd, "--formatter", "json", *ctx.rel_files]

        # Count initial issues.
        try:
            initial_success, check_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout, cwd=ctx.cwd)

        initial_issues = parse_stylelint_output(output=check_output)
        initial_count = len(initial_issues)
        if not initial_issues and self._is_no_config_error(check_output):
            return self._create_no_config_result(cwd=ctx.cwd)
        # A failing initial check with nothing parsed is a crash/config
        # error, not a fixable state.
        if not initial_issues and not initial_success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    check_output or "Stylelint exited with an error and no results."
                ),
                issues_count=0,
                cwd=ctx.cwd,
            )

        # Apply fixes.
        fix_cmd = [*base_cmd, "--fix", "--formatter", "json", *ctx.rel_files]
        logger.debug(f"[StylelintPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            fix_success, fix_output = self._run_subprocess(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout, cwd=ctx.cwd)

        # A crashed --fix run (nothing parseable) must surface, not flow into
        # a re-check that would misattribute the failure.
        if not fix_success and not parse_stylelint_output(output=fix_output):
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=fix_output or "Stylelint --fix exited with an error.",
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                cwd=ctx.cwd,
            )

        # Re-check for remaining issues.
        try:
            final_success, final_output = self._run_subprocess(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._create_timeout_result(timeout_val=ctx.timeout, cwd=ctx.cwd)

        remaining_issues = parse_stylelint_output(output=final_output)
        # A failed verification with nothing parsed must not read as
        # "all fixed" — keep the initial issues as remaining.
        if not remaining_issues and not final_success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(final_output or "Stylelint re-check exited with an error."),
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                cwd=ctx.cwd,
            )
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

        output_lines: list[str] = []
        if fixed_count > 0:
            output_lines.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            output_lines.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
            output_lines.extend(
                f"  {issue.file} - {issue.message}" for issue in remaining_issues[:5]
            )
            if len(remaining_issues) > 5:
                output_lines.append(f"  ... and {len(remaining_issues) - 5} more")
        elif remaining_count == 0 and fixed_count > 0:
            output_lines.append("All issues were successfully auto-fixed")

        if (
            merged_options.get("verbose_fix_output", False)
            and fix_output
            and fix_output.strip()
        ):
            output_lines.append(f"Fix output:\n{fix_output}")

        final_message = "\n".join(output_lines) if output_lines else None
        success = remaining_count == 0

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=final_message,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues if initial_issues else None,
            cwd=ctx.cwd,
        )
