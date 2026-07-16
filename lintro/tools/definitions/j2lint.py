"""j2lint tool definition.

j2lint (aristanetworks/j2lint) is a command-line linter for Jinja2 templates.
It checks non-HTML Jinja2 templates (e.g. ``*.j2`` used for YAML, TOML, or
config generation) against a set of best-practice rules covering indentation,
delimiter spacing, statement layout, and variable naming.
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
from lintro.parsers.j2lint.j2lint_parser import parse_j2lint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_list,
)

# Constants for j2lint configuration
J2LINT_DEFAULT_TIMEOUT: int = 30
J2LINT_DEFAULT_PRIORITY: int = 60
J2LINT_FILE_PATTERNS: list[str] = ["*.j2", "*.jinja", "*.jinja2"]


@register_tool
@dataclass
class J2lintPlugin(BaseToolPlugin):
    """j2lint Jinja2 template linter plugin.

    This plugin integrates j2lint with Lintro for checking non-HTML Jinja2
    templates against best-practice rules. It runs a single lint pass and
    reports issues; j2lint does not support automatic fixing.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="j2lint",
            description="Linter for non-HTML Jinja2 templates (best practices)",
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=J2LINT_FILE_PATTERNS,
            priority=J2LINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".j2lint.yaml"],
            version_command=["j2lint", "--version"],
            min_version=get_min_version(ToolName.J2LINT),
            default_options={
                "timeout": J2LINT_DEFAULT_TIMEOUT,
                "ignore": None,
                "warn": None,
            },
            default_timeout=J2LINT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        ignore: list[str] | None = None,
        warn: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Set j2lint-specific options.

        Args:
            ignore: Rule IDs to ignore entirely (e.g., ["S3", "V1"]).
            warn: Rule IDs to demote from errors to warnings.
            **kwargs: Other tool options.
        """
        validate_list(ignore, "ignore")
        validate_list(warn, "warn")

        options = filter_none_options(
            ignore=ignore,
            warn=warn,
        )
        super().set_options(**options, **kwargs)

    def _build_command(self, files: list[str]) -> list[str]:
        """Build the j2lint command.

        Rule-list options (``-i``/``-w``) use ``nargs='*'`` in j2lint, so a
        ``--`` separator is appended before the file arguments to terminate
        option parsing regardless of which options are present.

        Args:
            files: List of files to lint.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("j2lint") + ["--json"]

        ignore_opt = self.options.get("ignore")
        if isinstance(ignore_opt, list) and ignore_opt:
            cmd.append("-i")
            cmd.extend(str(rule) for rule in ignore_opt)

        warn_opt = self.options.get("warn")
        if isinstance(warn_opt, list) and warn_opt:
            cmd.append("-w")
            cmd.extend(str(rule) for rule in warn_opt)

        cmd.append("--")
        cmd.extend(files)
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return the j2lint documentation URL for the given code.

        j2lint documents all rules on a single page rather than per-code.

        Args:
            code: j2lint rule identifier (unused, single doc page).

        Returns:
            URL to the j2lint rules documentation, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.J2LINT

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Jinja2 templates with j2lint.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        if not ctx.files:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No Jinja2 template files found to check.",
                issues_count=0,
            )

        cmd = self._build_command(files=ctx.files)
        try:
            result = self._run_subprocess_result(cmd=cmd, timeout=ctx.timeout)
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"j2lint execution timed out ({ctx.timeout}s limit exceeded). "
                "Increase the timeout via --tool-options j2lint:timeout=N."
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:  # pragma: no cover
            logger.error(f"Failed to run j2lint: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"j2lint failed: {e}",
                issues_count=0,
            )

        # Parse stdout only: j2lint writes its ``--json`` report to stdout, and
        # mixing in stderr (warnings or tracebacks containing braces) can shift
        # the JSON object bounds and silently drop real findings. See #1043.
        issues = parse_j2lint_output(result.stdout)
        error_count = sum(1 for issue in issues if issue.level == "error")

        # j2lint exits non-zero when it reports lint errors, but also on a
        # genuine failure (bad arguments, crash, malformed output). If it failed
        # without producing any parseable issue, surface that as a tool failure
        # rather than a clean pass.
        if not result.success and not issues:
            logger.error(
                f"j2lint exited with code {result.returncode} and produced no "
                f"parseable output. stderr: {result.stderr.strip()}",
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    result.output
                    or "j2lint exited with an error but produced no output."
                ),
                issues_count=0,
            )

        return ToolResult(
            name=self.definition.name,
            success=error_count == 0,
            output=result.stdout if issues else None,
            issues_count=len(issues),
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """j2lint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: j2lint does not support fixing issues.
        """
        raise NotImplementedError(
            "j2lint cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
