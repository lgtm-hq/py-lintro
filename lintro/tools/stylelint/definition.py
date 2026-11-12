"""Stylelint tool definition.

Stylelint is a mighty, configurable linter for CSS, SCSS, Sass, and Less
stylesheets. It reports rule violations and syntax errors and can auto-fix
many of them via ``--fix``.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_outcome import (
    Checked,
    FixMeasured,
    FixUnmeasured,
    Skipped,
    TimedOut,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.parsers.stylelint.stylelint_parser import (
    parse_stylelint_output_with_stats,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.incomplete_run import (
    incomplete_run_result,
    malformed_report_reason,
    report_gap,
    tool_message_excerpt,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
    validate_str,
)

# Constants for Stylelint configuration
STYLELINT_DEFAULT_TIMEOUT: int = 30
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
            claims=[
                Claim(
                    patterns=STYLELINT_FILE_PATTERNS,
                    capabilities={Cap.FIX, Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
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
        # No configuration means no file was inspected by stylelint.
        return ToolResult.from_outcome(
            ToolOutcome(execution=Skipped("No stylelint configuration found")),
            name=self.definition.name,
            output=(
                "Skipping stylelint: no stylelint configuration found "
                "(e.g. .stylelintrc.json, stylelint.config.js, or a "
                '"stylelint" key in package.json). Add one to enable '
                "CSS/SCSS/Less linting."
            ),
            cwd=cwd,
        )

    def _create_timeout_result(
        self,
        timeout_val: float,
        cwd: str | None = None,
    ) -> ToolResult:
        """Create a ToolResult for timeout scenarios.

        Args:
            timeout_val: The timeout value that was exceeded.
            cwd: Working directory for the tool result.

        Follows the shared timeout accounting model (see
        :mod:`lintro.tools.core.timeout_utils`): the timeout is reported via
        ``timed_out=True`` and ``success=False`` rather than as a synthetic
        ``TIMEOUT`` pseudo-issue, so it never inflates the issue counts. Only
        genuine issues detected before the timeout are reported.

        Returns:
            ToolResult: ToolResult instance representing timeout failure.
        """
        timeout_msg = (
            f"Stylelint execution timed out ({timeout_val}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options stylelint:timeout=N"
        )
        return ToolResult.from_outcome(
            ToolOutcome(execution=TimedOut(timeout_msg)),
            name=self.definition.name,
            issues=[],
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
        cwd: str | Path | None = None,
    ) -> list[str]:
        """Build the base stylelint command, honoring an explicit config.

        Config discovery is otherwise delegated to stylelint itself, which
        resolves configuration per linted file (walking up from each file).

        Args:
            merged_options: Per-call options merged over the instance
                defaults; falls back to ``self.options`` when omitted so a
                caller-supplied ``config`` is not silently ignored.
            cwd: Directory stylelint will run in, so a project-local install
                resolves from the checked tree rather than lintro's own
                (#1727, #1811).

        Returns:
            The base command list.
        """
        cmd = [*self._get_executable_command(tool_name="stylelint", cwd=cwd)]
        opts = merged_options if merged_options is not None else self.options
        explicit = opts.get("config")
        if explicit:
            cmd.extend(["--config", str(explicit)])
        return cmd

    @staticmethod
    def _fix_stage_reasons(
        *,
        fix_failures: int,
        final_gap: tuple[str, int | None] | None,
    ) -> str:
        """Name every fix stage that left the verdict incomplete.

        A malformed ``--fix`` report must not hide a re-check that crashed
        or printed nothing, so each stage's reason is listed.

        Args:
            fix_failures: Malformed records in the ``--fix`` report.
            final_gap: The re-check's own gap, if it had one.

        Returns:
            One reason per incomplete stage, one per line.
        """
        reasons: list[str] = []
        if fix_failures:
            reasons.append(malformed_report_reason("Stylelint --fix", fix_failures))
        if final_gap is not None:
            reasons.append(final_gap[0])
        return "\n".join(reasons)

    def _fix_failure_result(
        self,
        *,
        reason: str,
        accounting: FixUnmeasured,
        issues: list[StylelintIssue],
        initial_issues: list[StylelintIssue] | None = None,
        parse_failures: int | None = None,
        cwd: str | None = None,
    ) -> ToolResult:
        """Build the result for a fix run that stopped without a verdict.

        Args:
            reason: Named reason the run is incomplete.
            accounting: Why fix accounting is unmeasured. A stage without a
                usable report measured nothing, so its counts are never
                guessed, and the run stays incomplete even if a later
                convergence retry is clean.
            issues: Findings Stylelint really reported, kept as they are.
            initial_issues: Pre-fix findings, when the fix never ran.
            parse_failures: Malformed report containers or records.
            cwd: Working directory Stylelint ran in.

        Returns:
            A failed result flagged as an execution failure, with inspection
            unknown (#3352).
        """
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=False, inspected_files=None),
                accounting=accounting,
                issues_count=len(issues),
            ),
            name=self.definition.name,
            output=reason,
            issues=issues,
            initial_issues=initial_issues or [],
            cwd=cwd,
            parse_failures_count=parse_failures,
            execution_failed=True,
            findings_complete=False,
        )

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

        ctx = self.prepare(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        cmd = [
            *self._base_command(merged_options, cwd=ctx.cwd),
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

        issues, parse_failures = parse_stylelint_output_with_stats(output=output)
        if not issues and self._is_no_config_error(output):
            return self._create_no_config_result(cwd=ctx.cwd)
        # Stylelint prints a JSON report on every completed run, ``[...]``
        # with empty ``warnings`` when clean, and exits 2 when violations
        # exist. No report, a malformed one, or any other failure with
        # nothing parsed (crash, invalid flag or config) is no verdict, and
        # proves nothing about which files were read (#3352).
        gap = report_gap(
            tool="Stylelint",
            output=output,
            run_success=run_success,
            found_issues=bool(issues),
            parse_failures=parse_failures,
        )
        if gap is not None:
            reason, failures = gap
            return incomplete_run_result(
                name=self.definition.name,
                reason=reason,
                issues=issues,
                parse_failures=failures,
                cwd=ctx.cwd,
            )
        issues_count = len(issues)
        success = issues_count == 0

        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=success, inspected_files=True),
                issues_count=issues_count,
            ),
            name=self.definition.name,
            output=output if not success else None,
            issues=issues,
            cwd=ctx.cwd,
            parse_failures_count=parse_failures,
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

        ctx = self.prepare(
            paths,
            merged_options,
            no_files_message="No files to fix.",
        )
        if isinstance(ctx, ToolResult):
            return ctx

        base_cmd = self._base_command(merged_options, cwd=ctx.cwd)
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

        initial_issues, initial_failures = parse_stylelint_output_with_stats(
            check_output,
        )
        initial_count = len(initial_issues)
        if not initial_issues and self._is_no_config_error(check_output):
            return self._create_no_config_result(cwd=ctx.cwd)
        # No initial report, a malformed one, or a failing check with
        # nothing parsed is a crash/config error, not a fixable state.
        initial_gap = report_gap(
            tool="Stylelint",
            output=check_output,
            run_success=initial_success,
            found_issues=bool(initial_issues),
            parse_failures=initial_failures,
        )
        if initial_gap is not None:
            return self._fix_failure_result(
                reason=initial_gap[0],
                accounting=FixUnmeasured("Invalid Stylelint initial report."),
                issues=initial_issues,
                initial_issues=initial_issues,
                parse_failures=initial_failures,
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
        fix_issues, fix_failures = parse_stylelint_output_with_stats(fix_output)
        if not fix_success and not fix_issues:
            return self._fix_failure_result(
                reason=(
                    "Stylelint --fix exited with an error and reported no "
                    f"findings: {tool_message_excerpt(fix_output)}"
                ),
                # A crashed --fix may have rewritten files before failing,
                # so what remains is unknown, not "everything".
                accounting=FixUnmeasured(
                    "Stylelint --fix failed without a report.",
                    initial_count,
                ),
                issues=initial_issues,
                parse_failures=fix_failures,
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

        remaining_issues, final_failures = parse_stylelint_output_with_stats(
            final_output,
        )
        parse_failures = fix_failures + final_failures
        # Judge the re-check on its own report, before --fix findings join it.
        final_gap = report_gap(
            tool="Stylelint re-check",
            output=final_output,
            run_success=final_success,
            found_issues=bool(remaining_issues),
            parse_failures=final_failures,
        )
        if fix_failures:
            remaining_issues.extend(i for i in fix_issues if i not in remaining_issues)
        # A failed verification with nothing parsed measured nothing: it is
        # neither "all fixed" nor "nothing fixed". Keep the initial findings
        # and leave the residual unknown (#3352).
        if final_gap is not None and not parse_failures:
            return self._fix_failure_result(
                reason=final_gap[0],
                accounting=FixUnmeasured(
                    "Stylelint re-check printed no usable report.",
                    initial_count,
                ),
                issues=initial_issues,
                cwd=ctx.cwd,
            )
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)
        # Rewrites can expose warnings not reported by the initial check.
        initial_count = max(initial_count, fixed_count + remaining_count)

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
        success = remaining_count == 0 and parse_failures == 0

        return ToolResult.from_outcome(
            ToolOutcome(
                # A malformed fix or re-check report proves no coverage.
                execution=Checked(
                    success=success,
                    inspected_files=None if parse_failures else True,
                ),
                accounting=(
                    FixUnmeasured(
                        "Invalid Stylelint verification report.",
                        initial_count,
                    )
                    if parse_failures
                    else FixMeasured(
                        initial=initial_count,
                        fixed=fixed_count,
                        remaining=remaining_count,
                    )
                ),
                issues_count=remaining_count,
            ),
            name=self.definition.name,
            output=(
                self._fix_stage_reasons(fix_failures=fix_failures, final_gap=final_gap)
                if parse_failures
                else final_message
            ),
            issues=remaining_issues,
            initial_issues=initial_issues,
            cwd=ctx.cwd,
            parse_failures_count=parse_failures,
            execution_failed=bool(parse_failures),
        )
