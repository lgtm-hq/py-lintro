"""RuboCop tool definition.

RuboCop is a Ruby static code analyzer (linter) and formatter based on the
community Ruby style guide. It ships an extensive rule set organized into
departments (Layout, Lint, Metrics, Naming, Security, Style) and can
autocorrect many offenses.
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
from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
)

# Constants for RuboCop configuration
RUBOCOP_DEFAULT_TIMEOUT: int = 60
RUBOCOP_DEFAULT_PRIORITY: int = 55
RUBOCOP_FILE_PATTERNS: list[str] = [
    "*.rb",
    "*.rake",
    "*.gemspec",
    "Gemfile",
    "Rakefile",
]


@register_tool
@dataclass
class RubocopPlugin(BaseToolPlugin):
    """RuboCop Ruby linter and formatter plugin.

    Integrates RuboCop with Lintro for linting and autocorrecting Ruby files.
    Runs with RuboCop's sensible defaults when no ``.rubocop.yml`` is present.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="rubocop",
            description="Ruby static code analyzer and formatter",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=RUBOCOP_FILE_PATTERNS,
            priority=RUBOCOP_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".rubocop.yml", ".rubocop.yaml"],
            version_command=["rubocop", "--version"],
            min_version=get_min_version(ToolName.RUBOCOP),
            default_options={
                "timeout": RUBOCOP_DEFAULT_TIMEOUT,
                "unsafe_fixes": False,
            },
            default_timeout=RUBOCOP_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        unsafe_fixes: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set RuboCop-specific options.

        Args:
            unsafe_fixes: When True, fix runs use ``--autocorrect-all`` (which
                includes unsafe cops that may change program semantics) instead
                of the default safe ``--autocorrect``. Defaults to False.
            **kwargs: Other base tool options.
        """
        validate_bool(unsafe_fixes, "unsafe_fixes")

        options = filter_none_options(
            unsafe_fixes=unsafe_fixes,
        )
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return the RuboCop documentation URL for a cop.

        RuboCop cop docs live at
        ``https://docs.rubocop.org/rubocop/cops_<department>.html`` with an
        anchor derived from the lower-cased cop name (department + cop, no
        slash). For example ``Layout/SpaceInsideParens`` resolves to
        ``cops_layout.html#layoutspaceinsideparens``.

        Args:
            code: Cop name (e.g., "Layout/SpaceInsideParens").

        Returns:
            URL to the cop documentation, or None when the code has no
            department prefix.
        """
        if not code or "/" not in code:
            return None
        department, cop = code.split("/", 1)
        anchor = f"{department}{cop}".replace("/", "").lower()
        base = DocUrlTemplate.RUBOCOP.format(department=department.lower())
        return f"{base}#{anchor}"

    def _build_check_command(self, rel_files: list[str]) -> list[str]:
        """Build the RuboCop check command (JSON output, no autocorrect).

        Args:
            rel_files: File paths to inspect, relative to the working directory.

        Returns:
            Full command argument list.
        """
        cmd = self._get_executable_command(tool_name="rubocop")
        cmd.extend(["--format", "json"])
        cmd.extend(rel_files)
        return cmd

    def _build_fix_command(self, rel_files: list[str]) -> list[str]:
        """Build the RuboCop autocorrect command.

        Uses safe ``--autocorrect`` by default, or ``--autocorrect-all`` when
        the ``unsafe_fixes`` option is enabled.

        Args:
            rel_files: File paths to autocorrect, relative to the working
                directory.

        Returns:
            Full command argument list.
        """
        cmd = self._get_executable_command(tool_name="rubocop")
        if self.options.get("unsafe_fixes"):
            cmd.append("--autocorrect-all")
        else:
            cmd.append("--autocorrect")
        cmd.extend(["--format", "json"])
        cmd.extend(rel_files)
        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Ruby files with RuboCop.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_check_command(ctx.rel_files)
        logger.debug(f"[RubocopPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")
        try:
            # Parse stdout only: RuboCop writes JSON to stdout but emits
            # "new cops not configured" notices to stderr, which would
            # otherwise corrupt the JSON payload (see issue #1043).
            result = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"RuboCop execution timed out ({ctx.timeout}s limit exceeded).",
                issues_count=1,
                issues=[],
                cwd=ctx.cwd,
            )

        issues = parse_rubocop_output(output=result.stdout)
        count = len(issues)

        # RuboCop exits 1 for offenses (parsed above from the JSON report);
        # any other failure with nothing parsed is a config/runtime error
        # that must not read as a clean pass.
        if not result.success and count == 0:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "RuboCop exited with an error and no results."
                ),
                issues_count=0,
                cwd=ctx.cwd,
            )

        return ToolResult(
            name=self.definition.name,
            success=count == 0,
            output=None if count == 0 else result.stdout,
            issues_count=count,
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Autocorrect Ruby files with RuboCop.

        Runs a check to record the initial offenses, applies autocorrection,
        then re-checks to determine the remaining offenses. The number of fixed
        offenses is ``initial - remaining``.

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

        check_cmd = self._build_check_command(ctx.rel_files)

        try:
            initial_result = self._run_subprocess_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._fix_timeout_result(ctx.timeout, ctx.cwd)
        initial_issues = parse_rubocop_output(output=initial_result.stdout)
        initial_count = len(initial_issues)

        fix_cmd = self._build_fix_command(ctx.rel_files)
        logger.debug(f"[RubocopPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            fix_result = self._run_subprocess_result(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._fix_timeout_result(ctx.timeout, ctx.cwd, initial_count)

        # --autocorrect exits 1 when offenses remain after correction (its
        # JSON report parses below via the re-check); anything else with no
        # parseable report is a crash that must surface, not read as a fix
        # pass with leftovers.
        if not fix_result.success and not parse_rubocop_output(
            output=fix_result.stdout,
        ):
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    fix_result.stderr.strip()
                    or fix_result.stdout.strip()
                    or "RuboCop autocorrect exited with an error."
                ),
                issues_count=initial_count,
                issues=initial_issues,
                initial_issues_count=initial_count,
                fixed_issues_count=0,
                remaining_issues_count=initial_count,
                cwd=ctx.cwd,
            )

        try:
            remaining_result = self._run_subprocess_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._fix_timeout_result(ctx.timeout, ctx.cwd, initial_count)
        remaining_issues = parse_rubocop_output(output=remaining_result.stdout)
        remaining_count = len(remaining_issues)

        fixed_count = max(0, initial_count - remaining_count)

        summary: list[str] = []
        if fixed_count > 0:
            summary.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        final_summary = "\n".join(summary) if summary else "No fixes applied."

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=final_summary,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues if initial_issues else None,
            cwd=ctx.cwd,
        )

    def _fix_timeout_result(
        self,
        timeout_val: int,
        cwd: str | None,
        initial_count: int = 0,
    ) -> ToolResult:
        """Build a standardized timeout result for fix operations.

        Args:
            timeout_val: The timeout value that was exceeded.
            cwd: Working directory for the tool result.
            initial_count: Initial issue count recorded before the timeout.

        Returns:
            ToolResult describing the timeout.
        """
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=f"RuboCop execution timed out ({timeout_val}s limit exceeded).",
            issues_count=0,
            issues=[],
            initial_issues_count=initial_count,
            cwd=cwd,
        )
