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
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.rubocop.rubocop_issue import RubocopIssue
from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output
from lintro.plugins.base import BaseToolPlugin, ExecutionContext
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.batch_runner import (
    BatchCheckPolicy,
    BatchOutput,
    BatchSuccess,
    batch_check_result,
    batch_fix_timeout_result,
    batch_timeout_result,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
)

# Constants for RuboCop configuration
RUBOCOP_DEFAULT_TIMEOUT: int = 60
#: Mirrors RuboCop's own default ``AllCops/Include`` list so lintro hands it
#: the same file set it would inspect when run directly — extensionless Ruby
#: DSL files included. ``*.ru`` covers ``config.ru``.
RUBOCOP_FILE_PATTERNS: list[str] = [
    "*.rb",
    "*.rake",
    "*.gemspec",
    "*.ru",
    "*.thor",
    "Appraisals",
    "Berksfile",
    "Brewfile",
    "Buildfile",
    "Capfile",
    "Cheffile",
    "Dangerfile",
    "Deliverfile",
    "Fastfile",
    "Gemfile",
    "Guardfile",
    "Jarfile",
    "Mavenfile",
    "Podfile",
    "Puppetfile",
    "Rakefile",
    "Snapfile",
    "Steepfile",
    "Thorfile",
    "Vagabondfile",
    "Vagrantfile",
]

#: RuboCop exits 1 to report offenses, which the JSON report accounts for, so
#: both halves must be clean: a non-zero exit with nothing parsed is a
#: config/runtime error that must not read as a pass. That is also the only
#: case where the raw text is the sole available diagnosis, so it is the only
#: case that surfaces it — offenses travel as parsed issues instead.
_CHECK_POLICY: BatchCheckPolicy = BatchCheckPolicy(
    success=BatchSuccess.EXIT_AND_ISSUES,
    output=BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES,
    report_cwd=True,
)


#: Departments RuboCop itself ships. Their cops are documented on the core
#: docs sub-site (``docs.rubocop.org/rubocop/``).
_CORE_DEPARTMENTS: frozenset[str] = frozenset(
    {
        "Bundler",
        "Gemspec",
        "Layout",
        "Lint",
        "Metrics",
        "Migration",
        "Naming",
        "Security",
        "Style",
    },
)

#: Extension departments that publish their cops on their own docs sub-site.
#: The value is the sub-site slug, which is the gem name and does not always
#: match the department (``FactoryBot`` ships as ``rubocop-factory_bot``).
#: An extension outside this table gets no URL rather than a core-site link
#: that would 404.
_EXTENSION_DOC_PROJECTS: dict[str, str] = {
    "Capybara": "rubocop-capybara",
    "FactoryBot": "rubocop-factory_bot",
    "Minitest": "rubocop-minitest",
    "Performance": "rubocop-performance",
    "RSpec": "rubocop-rspec",
    "Rails": "rubocop-rails",
    "ThreadSafety": "rubocop-thread_safety",
}


def _error_text(*, stdout: str, stderr: str, fallback: str) -> str:
    """Pick the most informative diagnostic text from a failed run.

    Args:
        stdout: Standard output captured from the command.
        stderr: Standard error captured from the command.
        fallback: Text to use when both streams are empty.

    Returns:
        The stderr notice, else the raw stdout, else ``fallback``.
    """
    return stderr.strip() or stdout.strip() or fallback


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
            claims=[
                Claim(
                    patterns=RUBOCOP_FILE_PATTERNS,
                    capabilities={Cap.FIX, Cap.FORMAT, Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
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

        Cop docs live at ``https://docs.rubocop.org/<project>/cops_
        <department>.html`` with an anchor derived from the lower-cased cop
        name (department + cop, no slash). ``<project>`` is ``rubocop`` for a
        core department and the extension gem's name for an extension one, so
        ``Layout/SpaceInsideParens`` resolves to
        ``rubocop/cops_layout.html#layoutspaceinsideparens`` while
        ``Rails/TimeZone`` resolves to
        ``rubocop-rails/cops_rails.html#railstimezone``.

        Args:
            code: Cop name (e.g., "Layout/SpaceInsideParens").

        Returns:
            URL to the cop documentation, or None when the code has no
            department prefix or names an extension whose docs sub-site is not
            known — a core-site link for such a cop would 404.
        """
        if not code or "/" not in code:
            return None
        department, cop = code.split("/", 1)
        if department in _CORE_DEPARTMENTS:
            project = "rubocop"
        else:
            extension_project = _EXTENSION_DOC_PROJECTS.get(department)
            if extension_project is None:
                return None
            project = extension_project
        anchor = f"{department}{cop}".replace("/", "").lower()
        base = DocUrlTemplate.RUBOCOP.format(
            project=project,
            department=department.lower(),
        )
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

    def _run_json(
        self,
        cmd: list[str],
        ctx: ExecutionContext,
    ) -> tuple[bool, str, str]:
        """Run one RuboCop invocation, keeping stdout separate from stderr.

        RuboCop writes its JSON report to stdout but emits "new cops not
        configured" notices to stderr, which would corrupt the payload if the
        streams were combined (see issue #1043). That is why this tool drives
        the batch helpers by hand instead of using ``run_batch_check`` /
        ``run_batch_fix``, both of which read the combined display output.

        The streams are unpacked here rather than handed back as the base
        class's result object, so this module needs no import from
        ``lintro.plugins`` beyond the three the layers contract allows.

        Args:
            cmd: Fully built command line.
            ctx: Prepared execution context supplying timeout and cwd.

        Returns:
            Tuple of (exited zero, stdout, stderr).
        """
        result = self._run_subprocess_result(
            cmd=cmd,
            timeout=ctx.timeout,
            cwd=ctx.cwd,
        )
        return result.success, result.stdout, result.stderr

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Ruby files with RuboCop.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        prepared = self.prepare(paths, options)
        if isinstance(prepared, ToolResult):
            return prepared
        ctx = prepared

        cmd = self._build_check_command(ctx.rel_files)
        logger.debug(f"[RubocopPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")
        try:
            exit_success, stdout, stderr = self._run_json(cmd, ctx)
        except subprocess.TimeoutExpired:
            return batch_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                cmd=cmd,
                cwd=ctx.cwd,
                issues=[],
            )

        issues = parse_rubocop_output(output=stdout)
        return batch_check_result(
            plugin=self,
            exit_success=exit_success,
            # Surface the diagnostic streams only; the JSON report is already
            # represented by the parsed issues.
            output=_error_text(
                stdout=stdout,
                stderr=stderr,
                fallback="RuboCop exited with an error and no results.",
            ),
            issues=issues,
            policy=_CHECK_POLICY,
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
        prepared = self.prepare(
            paths,
            options,
            no_files_message="No files to format.",
        )
        if isinstance(prepared, ToolResult):
            return prepared
        ctx = prepared

        check_cmd = self._build_check_command(ctx.rel_files)
        try:
            initial_ok, initial_stdout, initial_stderr = self._run_json(
                check_cmd,
                ctx,
            )
        except subprocess.TimeoutExpired:
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=[],
                cmd=check_cmd,
                cwd=ctx.cwd,
            )
        initial_issues = parse_rubocop_output(output=initial_stdout)
        # A non-zero exit with nothing parsed is a config/runtime error, not a
        # clean file: autocorrecting on top of it would rewrite sources RuboCop
        # never managed to inspect.
        if not initial_ok and not initial_issues:
            return self._fix_failure_result(
                output=_error_text(
                    stdout=initial_stdout,
                    stderr=initial_stderr,
                    fallback="RuboCop check exited with an error.",
                ),
                initial_issues=[],
                cwd=ctx.cwd,
            )

        fix_cmd = self._build_fix_command(ctx.rel_files)
        logger.debug(f"[RubocopPlugin] Fixing: {' '.join(fix_cmd)} (cwd={ctx.cwd})")
        try:
            fix_ok, fix_stdout, fix_stderr = self._run_json(fix_cmd, ctx)
        except subprocess.TimeoutExpired:
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=initial_issues,
                cmd=fix_cmd,
                cwd=ctx.cwd,
            )

        # --autocorrect exits 1 when offenses remain after correction (its
        # JSON report parses below via the re-check); anything else with no
        # parseable report is a crash that must surface, not read as a fix
        # pass with leftovers.
        if not fix_ok and not parse_rubocop_output(output=fix_stdout):
            return self._fix_failure_result(
                output=_error_text(
                    stdout=fix_stdout,
                    stderr=fix_stderr,
                    fallback="RuboCop autocorrect exited with an error.",
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
            )

        try:
            remaining_ok, remaining_stdout, remaining_stderr = self._run_json(
                check_cmd,
                ctx,
            )
        except subprocess.TimeoutExpired:
            return batch_fix_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                initial_issues=initial_issues,
                cmd=check_cmd,
                cwd=ctx.cwd,
            )
        remaining_issues = parse_rubocop_output(output=remaining_stdout)
        # Same fail-closed rule for the verification run: an unparseable
        # failure is not proof that every offense was corrected.
        if not remaining_ok and not remaining_issues:
            return self._fix_failure_result(
                output=_error_text(
                    stdout=remaining_stdout,
                    stderr=remaining_stderr,
                    fallback="RuboCop verification exited with an error.",
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
            )
        return self._fix_success_result(
            initial_issues=initial_issues,
            remaining_issues=remaining_issues,
            cwd=ctx.cwd,
        )

    def _fix_failure_result(
        self,
        *,
        output: str,
        initial_issues: list[RubocopIssue],
        cwd: str | None,
    ) -> ToolResult:
        """Build the result for an autocorrect run that crashed.

        Every offense detected before the fix ran is reported as still
        remaining, keeping ``initial == fixed + remaining`` intact.

        Args:
            output: Diagnostic text explaining the failure.
            initial_issues: Offenses detected before the fix ran.
            cwd: Working directory to record on the result.

        Returns:
            ToolResult describing the failed autocorrect run.
        """
        initial_count = len(initial_issues)
        return ToolResult(
            name=self.definition.name,
            success=False,
            output=output,
            issues_count=initial_count,
            issues=initial_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=0,
            remaining_issues_count=initial_count,
            initial_issues=initial_issues or None,
            cwd=cwd,
        )

    def _fix_success_result(
        self,
        *,
        initial_issues: list[RubocopIssue],
        remaining_issues: list[RubocopIssue],
        cwd: str | None,
    ) -> ToolResult:
        """Score an autocorrect run against its post-fix re-check.

        Args:
            initial_issues: Offenses detected before the fix ran.
            remaining_issues: Offenses the re-check still reports.
            cwd: Working directory to record on the result.

        Returns:
            ToolResult with the surviving offenses and the fix counts.
        """
        remaining_count = len(remaining_issues)
        fixed_count = max(0, len(initial_issues) - remaining_count)
        # A fix can surface findings the first pass did not see; grow the
        # initial total so ``initial == fixed + remaining`` stays valid, the
        # same guard the shared batch fix runner applies.
        initial_count = max(len(initial_issues), fixed_count + remaining_count)

        summary: list[str] = []
        if fixed_count > 0:
            summary.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        final_summary = "\n".join(summary) if summary else "No fixes applied."

        logger.debug(
            f"[RubocopPlugin] Fix complete: initial={initial_count}, "
            f"fixed={fixed_count}, remaining={remaining_count}",
        )

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=final_summary,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues or None,
            cwd=cwd,
        )
