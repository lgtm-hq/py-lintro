"""html-validate tool definition.

html-validate is an offline HTML validator that checks HTML documents against
best practices, standards, and accessibility (WCAG) rules. It is a Node.js tool
invoked via bun/bunx, mirroring lintro's other Node tools (markdownlint,
prettier).

No configuration is required: when no ``.htmlvalidate.*`` config is found,
html-validate applies its built-in ``html-validate:recommended`` preset, so the
tool produces sensible results out of the box.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.html_validate.html_validate_parser import (
    parse_html_validate_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.unified_config import DEFAULT_TOOL_PRIORITIES

# Constants for html-validate configuration
HTML_VALIDATE_DEFAULT_TIMEOUT: int = 30
HTML_VALIDATE_DEFAULT_PRIORITY: int = DEFAULT_TOOL_PRIORITIES.get("html_validate", 30)
HTML_VALIDATE_FILE_PATTERNS: list[str] = ["*.html", "*.htm", "*.vue", "*.svelte"]
HTML_VALIDATE_CONFIG_FILENAMES: list[str] = [
    ".htmlvalidate.json",
    ".htmlvalidate.js",
    ".htmlvalidate.cjs",
    ".htmlvalidate.mjs",
]


@register_tool
@dataclass
class HtmlValidatePlugin(BaseToolPlugin):
    """html-validate HTML validator plugin.

    Integrates html-validate with Lintro for checking HTML documents for
    validity, best practices, and accessibility issues. Check-only: the tool
    ships no autofixer.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="html_validate",
            description=(
                "Offline HTML validator for standards, best practices, and "
                "accessibility (WCAG) checks"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=HTML_VALIDATE_FILE_PATTERNS,
            priority=HTML_VALIDATE_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(HTML_VALIDATE_CONFIG_FILENAMES),
            version_command=["html-validate", "--version"],
            min_version=get_min_version(ToolName.HTML_VALIDATE),
            default_options={
                "timeout": HTML_VALIDATE_DEFAULT_TIMEOUT,
            },
            default_timeout=HTML_VALIDATE_DEFAULT_TIMEOUT,
        )

    def doc_url(self, code: str) -> str | None:
        """Return the html-validate documentation URL for a rule code.

        html-validate rule identifiers may be simple (``no-implicit-close``) or
        namespaced (``wcag/h37``); both map directly into the rule doc URL path.

        Args:
            code: html-validate rule identifier.

        Returns:
            URL to the rule documentation, or None when no code is available.
        """
        if code:
            return DocUrlTemplate.HTML_VALIDATE.format(code=code)
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with html-validate.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        logger.debug(
            f"[HtmlValidatePlugin] Discovered {len(ctx.files)} files matching "
            f"patterns: {self.definition.file_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[HtmlValidatePlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[HtmlValidatePlugin] Working directory: {ctx.cwd}")

        # Build command: resolve executable (bunx/npx/direct) + JSON formatter.
        cmd: list[str] = self._get_executable_command(tool_name="html_validate")
        cmd.extend(["--formatter", "json"])
        cmd.extend(ctx.rel_files)

        logger.debug(
            f"[HtmlValidatePlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})",
        )

        try:
            result = self._run_subprocess_result(
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
                cwd=ctx.cwd,
            )

        # JSON is written to stdout; stderr carries diagnostics only.
        issues = parse_html_validate_output(output=result.stdout)
        issues_count: int = len(issues)
        success_flag: bool = result.success and issues_count == 0

        # Suppress output when no issues found; otherwise surface the raw
        # combined output for diagnostics (e.g. a crash producing no JSON).
        final_output: str | None = None if success_flag else result.output

        return ToolResult(
            name=self.definition.name,
            success=success_flag,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """html-validate cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: html-validate is a validator only and cannot
                fix issues.
        """
        raise NotImplementedError(
            "html-validate cannot fix issues; it only validates HTML.",
        )
