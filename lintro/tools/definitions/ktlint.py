"""ktlint tool definition.

ktlint is an anti-bikeshedding Kotlin linter with a built-in formatter. It
enforces the official Kotlin coding conventions (and the Android Kotlin Style
Guide) with minimal configuration, honors ``.editorconfig``, and can
auto-correct most violations via ``--format``.
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
from lintro.parsers.ktlint.ktlint_parser import parse_ktlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.core.option_validators import filter_none_options, validate_str

# Constants for ktlint configuration
KTLINT_DEFAULT_TIMEOUT: int = 60
KTLINT_DEFAULT_PRIORITY: int = 50
KTLINT_FILE_PATTERNS: list[str] = ["*.kt", "*.kts"]
KTLINT_DEFAULT_REPORTER: str = "json"

# Valid code styles accepted by ``ktlint --code-style=...``.
KTLINT_CODE_STYLES: tuple[str, ...] = (
    "android_studio",
    "intellij_idea",
    "ktlint_official",
)

# Rulesets ktlint ships with; used to route documentation URLs.
KTLINT_RULESETS: frozenset[str] = frozenset({"standard", "experimental"})


def normalize_ktlint_code_style(value: str) -> str:
    """Normalize and validate a ktlint code style.

    Args:
        value: Code style string to normalize.

    Returns:
        Normalized (lowercase) code style string.

    Raises:
        ValueError: If the code style is not a valid ktlint code style.
    """
    normalized = value.lower()
    if normalized not in KTLINT_CODE_STYLES:
        valid = ", ".join(KTLINT_CODE_STYLES)
        raise ValueError(f"Invalid code style: {value!r}. Valid styles: {valid}")
    return normalized


@register_tool
@dataclass
class KtlintPlugin(BaseToolPlugin):
    """ktlint Kotlin linter and formatter plugin.

    This plugin integrates ktlint with Lintro for linting and formatting
    Kotlin (``.kt``) and Kotlin Script (``.kts``) files. ktlint carries a
    heavy JVM startup cost, so files are checked in a single batch
    invocation rather than one process per file.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="ktlint",
            description=("Anti-bikeshedding Kotlin linter with a built-in formatter"),
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=KTLINT_FILE_PATTERNS,
            priority=KTLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".editorconfig"],
            version_command=["ktlint", "--version"],
            min_version=get_min_version(ToolName.KTLINT),
            default_options={
                "timeout": KTLINT_DEFAULT_TIMEOUT,
                "code_style": None,
                "editorconfig": None,
            },
            default_timeout=KTLINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        code_style: str | None = None,
        editorconfig: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set ktlint-specific options.

        Args:
            code_style: Code style to enforce (android_studio, intellij_idea,
                or ktlint_official). When unset, ktlint uses its default
                (``ktlint_official``) unless ``.editorconfig`` overrides it.
            editorconfig: Path to a default ``.editorconfig`` used when no
                ``.editorconfig`` is found on the path to a scanned file.
            **kwargs: Other tool options.

        Raises:
            ValueError: If ``code_style`` is not a valid ktlint code style.
        """
        if code_style is not None:
            code_style = normalize_ktlint_code_style(code_style)
        validate_str(editorconfig, "editorconfig")

        options = filter_none_options(
            code_style=code_style,
            editorconfig=editorconfig,
        )
        super().set_options(**options, **kwargs)

    def doc_url(self, code: str) -> str | None:
        """Return the ktlint rules documentation URL for a rule id.

        ktlint rule ids are namespaced by ruleset (e.g. ``standard:filename``
        or ``experimental:...``). The public docs group rules by ruleset
        rather than per-rule anchor, so this routes to the correct ruleset
        page.

        Args:
            code: ktlint rule id (e.g. "standard:filename").

        Returns:
            URL to the rule's ruleset documentation page, or None if empty.
        """
        if not code:
            return None
        ruleset = code.split(":", 1)[0] if ":" in code else "standard"
        if ruleset not in KTLINT_RULESETS:
            ruleset = "standard"
        return DocUrlTemplate.KTLINT.format(ruleset=ruleset)

    def _build_common_args(self) -> list[str]:
        """Build CLI arguments shared by check and fix invocations.

        Returns:
            CLI arguments for ktlint (excluding the executable, reporter,
            and file paths).
        """
        # ``--log-level=error`` suppresses ktlint's warn-level log lines that
        # would otherwise be emitted to stdout ahead of the JSON report.
        args: list[str] = ["--log-level=error"]

        code_style = self.options.get("code_style")
        if code_style is not None:
            args.append(f"--code-style={code_style}")

        editorconfig = self.options.get("editorconfig")
        if editorconfig is not None:
            args.append(f"--editorconfig={editorconfig}")

        return args

    def _run_ktlint(
        self,
        files: list[str],
        timeout: int,
        cwd: str | None,
        *,
        fix: bool,
    ) -> SubprocessResult:
        """Run ktlint over ``files`` in a single batch invocation.

        Args:
            files: File paths (relative to ``cwd``) to process.
            timeout: Timeout in seconds for the ktlint command.
            cwd: Working directory for command execution.
            fix: When True, pass ``--format`` to auto-correct in place.

        Returns:
            SubprocessResult with separated stdout/stderr so the JSON report
            on stdout can be parsed independently of log output on stderr.
        """
        cmd = [
            *self._get_executable_command(tool_name="ktlint"),
            f"--reporter={KTLINT_DEFAULT_REPORTER}",
            *self._build_common_args(),
        ]
        if fix:
            cmd.append("--format")
        cmd.extend(files)
        return self._run_subprocess_result(cmd=cmd, timeout=timeout, cwd=cwd)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with ktlint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        try:
            result = self._run_ktlint(
                files=ctx.rel_files,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                fix=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"ktlint timed out after {ctx.timeout}s.",
                issues_count=1,
            )

        issues = parse_ktlint_output(result.stdout)

        # ktlint exits non-zero whenever it finds lint errors (expected). A
        # non-zero exit with no parsed issues indicates a real execution
        # failure (e.g. a missing JVM), so surface the raw output instead of
        # reporting a clean file.
        if result.returncode != 0 and not issues:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=result.output or "ktlint execution failed.",
                issues_count=1,
            )

        return ToolResult(
            name=self.definition.name,
            success=len(issues) == 0,
            output=None,
            issues_count=len(issues),
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files with ktlint ``--format``.

        Runs an initial check to count issues, applies ``--format`` to
        auto-correct in place, then re-checks to count what remains. This
        preserves the fix invariant
        ``initial = fixed + remaining``; ktlint's JSON reporter does not
        expose per-issue fixability, so fixed count is derived from the
        before/after difference.

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

        try:
            # 1. Count issues before fixing.
            initial_result = self._run_ktlint(
                files=ctx.rel_files,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                fix=False,
            )
            initial_issues: list[BaseIssue] = list(
                parse_ktlint_output(initial_result.stdout),
            )

            # A non-zero exit with no parsed issues is a real failure.
            if initial_result.returncode != 0 and not initial_issues:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=initial_result.output or "ktlint execution failed.",
                    issues_count=1,
                )

            # 2. Apply auto-corrections in place. ktlint --format exits
            # non-zero when unfixable findings remain (counted by the
            # re-check below), so only a failure with no parseable report
            # signals a real formatter crash that must surface.
            format_result = self._run_ktlint(
                files=ctx.rel_files,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                fix=True,
            )
            if format_result.returncode != 0 and not parse_ktlint_output(
                format_result.stdout,
            ):
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=(
                        format_result.output or "ktlint --format exited with an error."
                    ),
                    issues_count=len(initial_issues),
                    issues=initial_issues,
                    initial_issues_count=len(initial_issues),
                    fixed_issues_count=0,
                    remaining_issues_count=len(initial_issues),
                )

            # 3. Re-check to count remaining (non-auto-correctable) issues.
            # A failed re-check with no parseable report must not read as
            # "everything fixed" — surface the verification failure instead.
            remaining_result = self._run_ktlint(
                files=ctx.rel_files,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                fix=False,
            )
            remaining_issues: list[BaseIssue] = list(
                parse_ktlint_output(remaining_result.stdout),
            )
            if remaining_result.returncode != 0 and not remaining_issues:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=(
                        remaining_result.output
                        or "ktlint re-check exited with an error."
                    ),
                    issues_count=len(initial_issues),
                    issues=initial_issues,
                    initial_issues_count=len(initial_issues),
                    fixed_issues_count=0,
                    remaining_issues_count=len(initial_issues),
                )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"ktlint timed out after {ctx.timeout}s.",
                issues_count=1,
            )

        initial_count = len(initial_issues)
        remaining_count = len(remaining_issues)
        # Formatting can expose findings the initial check did not report
        # (e.g. rules that only fire on the rewritten layout). Keep the
        # invariant initial = fixed + remaining consistent by growing the
        # initial count rather than letting remaining exceed it.
        if remaining_count > initial_count:
            initial_count = remaining_count
        fixed_count = max(0, initial_count - remaining_count)

        summary_parts: list[str] = []
        if fixed_count > 0:
            summary_parts.append(f"Fixed {fixed_count} issue(s)")
        if remaining_count > 0:
            summary_parts.append(
                f"Found {remaining_count} issue(s) that cannot be auto-fixed",
            )
        summary = "\n".join(summary_parts) if summary_parts else "No fixes needed."

        logger.debug(
            f"[KtlintPlugin] Fix complete: initial={initial_count}, "
            f"fixed={fixed_count}, remaining={remaining_count}",
        )

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=summary,
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues if initial_issues else None,
        )
