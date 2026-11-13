"""SQLFluff tool definition.

SQLFluff is a SQL linter and formatter with support for many SQL dialects.
It parses SQL into an AST and performs linting rules on top of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import PurePath
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import FixMeasured, NoFiles, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.sqlfluff.sqlfluff_issue import SqlfluffIssue
from lintro.parsers.sqlfluff.sqlfluff_parser import parse_sqlfluff_output_with_stats
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.check_runner import PerFileCheckPolicy, run_per_file_check
from lintro.tools.core.fix_runner import (
    FixStage,
    FixStageError,
    PerFileFixPolicy,
    run_per_file_fix,
)
from lintro.tools.core.incomplete_run import (
    malformed_report_reason,
    tool_message_excerpt,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
    validate_str,
)

# Constants for SQLFluff configuration
SQLFLUFF_DEFAULT_TIMEOUT: int = 60
SQLFLUFF_FILE_PATTERNS: list[str] = ["*.sql"]
SQLFLUFF_DEFAULT_FORMAT: str = "json"
SQLFLUFF_ALL_SKIPPED_MESSAGE: str = (
    "No files to lint: SQLFluff skipped every file given (an ignore file such "
    "as .sqlfluffignore, or its large_file_skip_byte_limit); none was inspected."
)


def _skip_reason(stderr: str, *, path: str) -> str:
    """Quote the warning SQLFluff printed when it skipped a file.

    SQLFluff names the file by the path it was given, which is absolute. That
    path is shortened to the file name first, so the quote's length cap keeps
    the part that says why the file was skipped.

    Args:
        stderr: The skipped invocation's diagnostics stream.
        path: The file passed to SQLFluff.

    Returns:
        SQLFluff's own warning without its log-level prefix, or an empty
        string when it printed none.
    """
    if not stderr.strip():
        return ""
    excerpt = tool_message_excerpt(stderr.replace(path, PurePath(path).name))
    level, _, rest = excerpt.partition(" ")
    return rest.strip() if level == "WARNING" and rest.strip() else excerpt


def _is_json(text: str) -> bool:
    """Report whether text decodes as JSON.

    Args:
        text: A command's stdout.

    Returns:
        True when the whole text is one JSON value.
    """
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _is_empty_report(text: str) -> bool:
    """Report whether a lint report is an empty JSON array.

    Args:
        text: One ``sqlfluff lint`` invocation's stdout.

    Returns:
        True when the whole text decodes to ``[]``.
    """
    try:
        return bool(json.loads(text) == [])
    except ValueError:
        return False


def _parse_report(output: str, failures: list[int]) -> list[SqlfluffIssue]:
    """Keep usable findings while recording incomplete per-file reports.

    Args:
        output: One file's JSON report, never empty: an empty one is no report
            and is rejected before parsing.
        failures: Per-invocation failure counts for the enclosing run.

    Returns:
        All usable findings in the report.

    Raises:
        ValueError: When no usable findings survived an invalid report.
    """
    issues, count = parse_sqlfluff_output_with_stats(output)
    failures.append(count)
    if count and not issues:
        raise ValueError(malformed_report_reason("SQLFluff", count))
    return issues


@register_tool
@dataclass
class SqlfluffPlugin(BaseToolPlugin):
    """SQLFluff SQL linter and formatter plugin.

    This plugin integrates SQLFluff with Lintro for linting and formatting
    SQL files.
    """

    _lint_diagnostics: list[str] = field(default_factory=list, init=False, repr=False)
    _lint_skipped: dict[str, str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="sqlfluff",
            description="SQL linter and formatter with dialect support",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=SQLFLUFF_FILE_PATTERNS,
            claims=[
                Claim(
                    patterns=SQLFLUFF_FILE_PATTERNS,
                    capabilities={Cap.FIX, Cap.FORMAT, Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
            native_configs=[".sqlfluff", "pyproject.toml"],
            version_command=["sqlfluff", "--version"],
            min_version=get_min_version(ToolName.SQLFLUFF),
            default_options={
                "timeout": SQLFLUFF_DEFAULT_TIMEOUT,
                "dialect": None,
                "exclude_rules": None,
                "rules": None,
                "templater": None,
            },
            default_timeout=SQLFLUFF_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        dialect: str | None = None,
        exclude_rules: list[str] | None = None,
        rules: list[str] | None = None,
        templater: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set SQLFluff-specific options.

        Args:
            dialect: SQL dialect (ansi, bigquery, postgres, mysql, snowflake,
                sqlite, etc.).
            exclude_rules: List of rules to exclude.
            rules: List of rules to include.
            templater: Templater to use (raw, jinja, python, placeholder).
            **kwargs: Other tool options.
        """
        validate_str(dialect, "dialect")
        validate_list(exclude_rules, "exclude_rules")
        validate_list(rules, "rules")
        validate_str(templater, "templater")

        options = filter_none_options(
            dialect=dialect,
            exclude_rules=exclude_rules,
            rules=rules,
            templater=templater,
        )
        super().set_options(**options, **kwargs)

    def _build_lint_command(self, files: list[str]) -> list[str]:
        """Build the sqlfluff lint command.

        Args:
            files: List of files to lint.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["sqlfluff", "lint", "--format", SQLFLUFF_DEFAULT_FORMAT]

        # Add dialect option
        dialect_opt = self.options.get("dialect")
        if dialect_opt is not None:
            cmd.extend(["--dialect", str(dialect_opt)])

        # Add exclude rules (comma-separated per SQLFluff CLI docs)
        exclude_rules_opt = self.options.get("exclude_rules")
        if isinstance(exclude_rules_opt, list) and exclude_rules_opt:
            cmd.extend(["--exclude-rules", ",".join(map(str, exclude_rules_opt))])

        # Add rules (comma-separated per SQLFluff CLI docs)
        rules_opt = self.options.get("rules")
        if isinstance(rules_opt, list) and rules_opt:
            cmd.extend(["--rules", ",".join(map(str, rules_opt))])

        # Add templater
        templater_opt = self.options.get("templater")
        if templater_opt is not None:
            cmd.extend(["--templater", str(templater_opt)])

        # Add end-of-options separator to handle filenames starting with '-'
        cmd.append("--")

        # Add files
        cmd.extend(files)

        return cmd

    def _build_fix_command(self, files: list[str]) -> list[str]:
        """Build the sqlfluff fix command.

        Args:
            files: List of files to fix.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["sqlfluff", "fix", "--force"]

        # Add dialect option
        dialect_opt = self.options.get("dialect")
        if dialect_opt is not None:
            cmd.extend(["--dialect", str(dialect_opt)])

        # Add exclude rules (comma-separated per SQLFluff CLI docs)
        exclude_rules_opt = self.options.get("exclude_rules")
        if isinstance(exclude_rules_opt, list) and exclude_rules_opt:
            cmd.extend(["--exclude-rules", ",".join(map(str, exclude_rules_opt))])

        # Add rules (comma-separated per SQLFluff CLI docs)
        rules_opt = self.options.get("rules")
        if isinstance(rules_opt, list) and rules_opt:
            cmd.extend(["--rules", ",".join(map(str, rules_opt))])

        # Add templater
        templater_opt = self.options.get("templater")
        if templater_opt is not None:
            cmd.extend(["--templater", str(templater_opt)])

        # Add end-of-options separator to handle filenames starting with '-'
        cmd.append("--")

        # Add files
        cmd.extend(files)

        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return SQLFluff documentation URL for the given rule code.

        Args:
            code: SQLFluff rule code (e.g., "LT01").

        Returns:
            URL to the SQLFluff rule documentation.
        """
        if code:
            return DocUrlTemplate.SQLFLUFF.format(code=code)
        return None

    def _run_subprocess_result(
        self,
        cmd: list[str],
        timeout: int | float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        """Keep lint JSON separate from diagnostics for the per-file runners.

        Args:
            cmd: Command and arguments to run.
            timeout: Timeout in seconds.
            cwd: Working directory for the subprocess.
            env: Environment variables for the subprocess.
            stdin: Optional stdin handle.

        Returns:
            Native status and streams, with lint stdout alone as the parser
            input.

        Raises:
            RuntimeError: When ``sqlfluff lint`` printed no JSON report, so
                the per-file runner records an execution error naming
                SQLFluff's own message instead of parsing it.
        """
        result = super()._run_subprocess_result(cmd, timeout, cwd, env, stdin)
        if cmd[1:2] != ["lint"]:
            return result
        if not result.stdout.strip():
            # ``sqlfluff lint --format json`` prints a per-file record even
            # for a clean file, so empty stdout is no report at any exit,
            # e.g. a malformed ``.sqlfluff`` raising a traceback (#3352).
            detail = (
                f": {tool_message_excerpt(result.stderr)}"
                if result.stderr.strip()
                else "."
            )
            raise RuntimeError(
                f"SQLFluff printed no report (exit {result.returncode}){detail}",
            )
        if result.success and _is_empty_report(result.stdout):
            # Every linted file, even a clean or empty one, gets a record. A
            # clean exit with an empty array is SQLFluff skipping the one file
            # given: ``.sqlfluffignore`` excludes it, or it is larger than
            # ``large_file_skip_byte_limit`` (#3388). Its warning says which.
            self._lint_skipped[cmd[-1]] = _skip_reason(result.stderr, path=cmd[-1])
        if not result.success and result.stderr.strip():
            self._lint_diagnostics.append(f"{cmd[-1]}:\n{result.stderr}")
        if not result.success and not _is_json(result.stdout):
            # A configuration error such as an unknown dialect exits 2 with
            # its message on stdout. That is SQLFluff failing to run, not a
            # malformed report, so it never reaches the parser.
            raise RuntimeError(
                f"SQLFluff could not run (exit {result.returncode}): "
                f"{tool_message_excerpt(result.stdout)}",
            )
        # Only stdout is the report. Diagnostics stay out of the parser, so a
        # run that printed none is not counted as a malformed report.
        return replace(result, output=result.stdout)

    def _parse_lint_report(
        self,
        output: str,
        failures: list[int],
    ) -> list[SqlfluffIssue]:
        """Parse one lint report, treating an empty one as no report.

        Args:
            output: One lint invocation's stdout.
            failures: Per-invocation failure counts for the enclosing run.

        Returns:
            All usable findings in the report.

        Raises:
            RuntimeError: When the invocation printed no report.
        """
        if not output.strip():
            # ``sqlfluff lint --format json`` prints a per-file record even
            # for a clean file, so empty stdout is an execution failure, not
            # a clean pass and not a malformed report (#3352).
            raise RuntimeError("SQLFluff printed no report.")
        return _parse_report(output, failures)

    @staticmethod
    def _unmeasured_reason(
        result: ToolResult,
        *,
        errors: list[FixStageError],
        malformed_reports: int,
        parse_failures: int,
    ) -> str | None:
        """Name every reason this fix run's accounting was not measured.

        Args:
            result: The per-file runner's aggregated fix result.
            errors: Files whose lint or fix stage stopped on an execution
                error, as the runner recorded them (#3401).
            malformed_reports: Lint reports so malformed that no finding
                survived. The runner records each as a check error too.
            parse_failures: Malformed records across the lint reports.

        Returns:
            The reasons, execution failures first, or None when every lint
            pass gave a usable verdict and every fix completed.
        """
        lint_errors = sum(1 for error in errors if error.stage is FixStage.CHECK)
        reasons: list[str] = []
        if lint_errors > malformed_reports:
            reasons.append("SQLFluff could not run.")
        if any(error.stage is FixStage.FIX for error in errors):
            reasons.append("SQLFluff fix did not complete.")
        if parse_failures:
            reasons.append("SQLFluff returned an incomplete report.")
        if not reasons and result.timed_out:
            reasons.append("SQLFluff timed out before reporting.")
        return " ".join(reasons) or None

    def _isolate_execution_state(self) -> None:
        """Give this execution copy its own lint diagnostics.

        ``copy_for_execution`` copies the cached registry instance shallowly,
        so without this every concurrent execution would append to, and
        clear, one shared list (#3352).
        """
        super()._isolate_execution_state()
        self._lint_diagnostics = []
        self._lint_skipped = {}

    def _reset_lint_state(self) -> None:
        """Forget what earlier runs of this instance recorded."""
        self._lint_diagnostics.clear()
        self._lint_skipped.clear()

    def _all_skipped_result(
        self,
        *,
        result: ToolResult,
        files: list[str],
        fixing: bool,
    ) -> ToolResult | None:
        """Return the no-files result when SQLFluff skipped every file.

        A file SQLFluff skipped, because ``.sqlfluffignore`` excludes it or
        it is larger than ``large_file_skip_byte_limit``, is never claimed as
        inspected. When it skipped all of them, nothing was inspected: the
        run is "no files", not a clean pass (#3388). The reason quotes the
        first skip warning SQLFluff printed. A run that also failed, timed
        out or reported findings keeps its own result.

        Args:
            result: Aggregated check or fix outcome.
            files: Every file lintro passed to SQLFluff.
            fixing: Whether this is a fix run, which records zero counters.

        Returns:
            The no-files result, or None when SQLFluff linted any file.
        """
        if (
            not files
            or set(self._lint_skipped) != set(files)
            or result.execution_failed
            or result.timed_out
            or result.issues
        ):
            return None
        outcome = (
            ToolOutcome(
                execution=NoFiles(),
                accounting=FixMeasured(initial=0, fixed=0, remaining=0),
            )
            if fixing
            else ToolOutcome(execution=NoFiles())
        )
        warning = next(
            (self._lint_skipped[file] for file in files if self._lint_skipped[file]),
            "",
        )
        output = (
            f"{SQLFLUFF_ALL_SKIPPED_MESSAGE} SQLFluff: {warning}"
            if warning
            else SQLFLUFF_ALL_SKIPPED_MESSAGE
        )
        return ToolResult.from_outcome(
            outcome,
            name=self.definition.name,
            output=output,
            cwd=result.cwd,
        )

    def _with_lint_diagnostics(self, result: ToolResult) -> ToolResult:
        """Attach lint diagnostics without feeding them through the JSON parser.

        Args:
            result: Aggregated check or fix outcome.

        Returns:
            The outcome with diagnostics from every failed lint invocation.
        """
        if not self._lint_diagnostics:
            return result
        output = "\n".join([result.output or "", *self._lint_diagnostics]).lstrip("\n")
        return replace(result, output=output)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with SQLFluff.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        self._reset_lint_state()
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx

        failures: list[int] = []
        result = run_per_file_check(
            ctx,
            plugin=self,
            command=lambda f: self._build_lint_command(files=[str(f)]),
            parse=lambda output: self._parse_lint_report(output, failures),
            policy=PerFileCheckPolicy(issues_imply_failure=True),
        )
        skipped = self._all_skipped_result(result=result, files=ctx.files, fixing=False)
        if skipped is not None:
            return skipped
        return self._with_lint_diagnostics(
            replace(
                result,
                success=result.success and not any(failures),
                parse_failures_count=sum(failures),
            ),
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files with SQLFluff.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Use shared preparation for version check, path validation, file discovery
        self._reset_lint_state()
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx

        failures: list[int] = []
        malformed: list[str] = []
        errors: list[FixStageError] = []

        def parse(output: str) -> list[SqlfluffIssue]:
            """Parse one lint report, noting one too malformed to use.

            Args:
                output: One lint invocation's stdout.

            Returns:
                All usable findings in the report.

            Raises:
                ValueError: When no usable finding survived the report.
            """
            try:
                return self._parse_lint_report(output, failures)
            except ValueError:
                malformed.append(output)
                raise

        result = run_per_file_fix(
            ctx,
            plugin=self,
            check_command=lambda f: self._build_lint_command(files=[str(f)]),
            fix_command=lambda f: self._build_fix_command(files=[str(f)]),
            parse=parse,
            errors=errors,
            policy=PerFileFixPolicy(
                check_failure_message="sqlfluff lint failed before fix",
                # No self-verification. sqlfluff fix can partially apply fixes
                # while exiting non-zero, so the surviving issues can only come
                # from a fresh lint pass — and since #1743 that pass is the
                # run-level verify pass, which also sees a later tool undoing
                # this one's work. A failed fix command still reports every
                # initial issue as remaining, so the pre-verify number errs
                # towards over-reporting rather than under-reporting.
                summarize=False,
                # ``sqlfluff fix`` exits 1 when unfixable violations remain.
                # It also exits 1 with a traceback on stderr when it cannot
                # write; the runner reads that diagnostic as an error (#3401).
                fix_findings_exit_codes=frozenset({1}),
            ),
        )
        skipped = self._all_skipped_result(result=result, files=ctx.files, fixing=True)
        if skipped is not None:
            return skipped
        unmeasured = self._unmeasured_reason(
            result,
            errors=errors,
            malformed_reports=len(malformed),
            parse_failures=sum(failures),
        )
        if unmeasured is not None:
            # A lint pass without a usable report or verdict measured
            # nothing, so its file is neither clean nor fixed (#3352). The
            # runner already leaves an errored run unmeasured (#3401); this
            # names every reason and covers malformed records, which the
            # runner cannot see.
            result = replace(
                result,
                success=False,
                parse_failures_count=sum(failures),
                residual_unknown=True,
                residual_unknown_reason=unmeasured,
                fixed_issues_count=None,
                remaining_issues_count=None,
                execution_failed=True,
            )
        return self._with_lint_diagnostics(result)
