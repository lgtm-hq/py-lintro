"""Vale tool definition.

Vale is a syntax-aware linter for prose and documentation. It checks Markdown,
AsciiDoc, reStructuredText, HTML, and plain text against configurable style
guides (Microsoft, Google, write-good, proselint, and custom rules).

Vale requires a ``.vale.ini`` configuration to run. When no configuration is
resolvable, this plugin skips as a non-error (rather than surfacing Vale's hard
``E100`` runtime error) so ``lintro check`` stays clean on projects that do not
use Vale.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.vale.vale_parser import parse_vale_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
    validate_str,
)
from lintro.tools.core.timeout_utils import create_timeout_result

# Constants for Vale configuration
VALE_DEFAULT_TIMEOUT: int = 30
VALE_DEFAULT_PRIORITY: int = 50
VALE_FILE_PATTERNS: list[str] = ["*.md", "*.rst", "*.adoc", "*.txt"]
VALE_CONFIG_FILENAMES: list[str] = [".vale.ini", "_vale.ini", "vale.ini"]


@register_tool
@dataclass
class ValePlugin(BaseToolPlugin):
    """Vale prose/documentation linter plugin.

    Integrates Vale with Lintro for linting prose in Markdown, reStructuredText,
    AsciiDoc, and plain-text files against configurable style guides.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="vale",
            description=(
                "Syntax-aware prose linter for docs with configurable style guides"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.DOCUMENTATION,
            file_patterns=VALE_FILE_PATTERNS,
            priority=VALE_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(VALE_CONFIG_FILENAMES),
            version_command=["vale", "--version"],
            min_version=get_min_version(ToolName.VALE),
            default_options={
                "timeout": VALE_DEFAULT_TIMEOUT,
                "config": None,
                "min_alert_level": None,
            },
            default_timeout=VALE_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        config: str | None = None,
        min_alert_level: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Vale-specific options.

        Args:
            config: Path to a Vale config file (maps to ``--config``).
            min_alert_level: Minimum alert level to report
                (``suggestion``, ``warning``, or ``error``).
            timeout: Timeout in seconds (default: 30).
            **kwargs: Other tool options.
        """
        validate_str(config, "config")
        validate_str(min_alert_level, "min_alert_level")
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(
            config=config,
            min_alert_level=min_alert_level,
            timeout=timeout,
        )
        super().set_options(**options, **kwargs)

    @staticmethod
    def _is_no_config_error(output: str | None) -> bool:
        """Report whether output indicates a missing Vale configuration.

        Vale requires a ``.vale.ini`` to run and exits with an ``E100``
        runtime error when none is resolvable. Detect that so lintro can skip
        gracefully rather than surfacing a hard failure.

        Args:
            output: Combined stdout/stderr from Vale.

        Returns:
            True if the output signals a missing/unresolvable configuration.
        """
        if not output:
            return False
        return (
            "E100" in output
            or ".vale.ini not found" in output
            or "no config file found" in output
        )

    def _create_no_config_result(self) -> ToolResult:
        """Create a skip ToolResult for when no Vale config is found.

        Vale cannot lint without a configuration, so lintro skips it (as a
        non-error) rather than surfacing a hard failure. This keeps runs clean
        for projects that do not use Vale.

        Returns:
            ToolResult: Skip result (success=True) with a helpful message.
        """
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=(
                "Skipping vale: no Vale configuration found "
                "(e.g. .vale.ini). Add one to enable prose linting."
            ),
            issues_count=0,
        )

    def _build_command(self) -> list[str]:
        """Build the base Vale command, honoring explicit options.

        Config discovery is otherwise delegated to Vale itself, which resolves
        a ``.vale.ini`` by walking up from each linted file.

        Returns:
            The base command list.
        """
        cmd: list[str] = ["vale", "--output=JSON"]

        config_opt = self.options.get("config")
        if config_opt:
            cmd.extend(["--config", str(config_opt)])

        level_opt = self.options.get("min_alert_level")
        if level_opt:
            cmd.extend(["--minAlertLevel", str(level_opt)])

        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Vale.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_command() + list(ctx.rel_files)
        logger.debug(f"[ValePlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

        try:
            _success, output = self._run_subprocess(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_result = create_timeout_result(
                tool=self,
                timeout=ctx.timeout,
                cmd=cmd,
            )
            return ToolResult(
                name=self.definition.name,
                success=timeout_result.success,
                output=timeout_result.output,
                issues_count=timeout_result.issues_count,
            )

        issues = parse_vale_output(output=output)
        if not issues and self._is_no_config_error(output):
            return self._create_no_config_result()

        # Vale exits 0 on a clean run and 1 when alerts are found; any other
        # non-zero exit with no parseable alerts is a runtime problem (invalid
        # config, missing styles package, bad flag). Surface that as a failure
        # instead of reporting a clean pass from a run that produced nothing.
        if not issues and not _success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(output or "Vale exited with an error and produced no results."),
                issues_count=0,
                parse_failures_count=1,
            )

        issues_count = len(issues)
        success = issues_count == 0

        return ToolResult(
            name=self.definition.name,
            success=success,
            output=output if not success else None,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Vale cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Vale is a linter only and cannot fix issues.
        """
        raise NotImplementedError(
            "Vale cannot fix issues; it reports prose and style violations only.",
        )
