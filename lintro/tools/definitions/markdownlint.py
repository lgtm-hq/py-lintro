"""Markdownlint tool definition.

Markdownlint-cli2 is a linter for Markdown files that checks for style
issues and best practices. It helps maintain consistent formatting
across documentation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - used safely with shell disabled
import tempfile
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.markdownlint.markdownlint_parser import parse_markdownlint_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import validate_positive_int
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.config import get_central_line_length
from lintro.utils.unified_config import DEFAULT_TOOL_PRIORITIES

# Constants for Markdownlint configuration
MARKDOWNLINT_DEFAULT_TIMEOUT: int = 30
MARKDOWNLINT_DEFAULT_PRIORITY: int = DEFAULT_TOOL_PRIORITIES.get("markdownlint", 30)
MARKDOWNLINT_FILE_PATTERNS: list[str] = ["*.md", "*.markdown"]


@register_tool
@dataclass
class MarkdownlintPlugin(BaseToolPlugin):
    """Markdownlint Markdown linter plugin.

    This plugin integrates markdownlint-cli2 with Lintro for checking
    Markdown files for style and formatting issues.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="markdownlint",
            description=("Markdown linter for style checking and best practices"),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=MARKDOWNLINT_FILE_PATTERNS,
            priority=MARKDOWNLINT_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                ".markdownlint.json",
                ".markdownlint.yaml",
                ".markdownlint.yml",
                ".markdownlint-cli2.jsonc",
                ".markdownlint-cli2.yaml",
            ],
            version_command=["markdownlint-cli2", "--help"],
            min_version=get_min_version(ToolName.MARKDOWNLINT),
            default_options={
                "timeout": MARKDOWNLINT_DEFAULT_TIMEOUT,
                "line_length": None,
            },
            default_timeout=MARKDOWNLINT_DEFAULT_TIMEOUT,
        )

    def _verify_tool_version(self) -> ToolResult | None:
        """Verify that markdownlint-cli2 meets minimum version requirements.

        Overrides base implementation to use the correct executable name.

        Returns:
            Optional[ToolResult]: None if version check passes, or a skip result
                if it fails.
        """
        from lintro.tools.core.version_requirements import check_tool_version

        # Use the correct command for markdownlint-cli2
        command = self._get_markdownlint_command()
        version_info = check_tool_version(self.definition.name, command)

        if version_info.version_check_passed:
            return None  # Version check passed

        # Version check failed - return skip result with warning
        skip_message = (
            f"Skipping {self.definition.name}: {version_info.error_message}. "
            f"Minimum required: {version_info.min_version}. "
            f"{version_info.install_hint}"
        )

        return ToolResult(
            name=self.definition.name,
            success=True,  # Not an error, just skipping
            output=skip_message,
            issues_count=0,
        )

    def set_options(  # type: ignore[override]
        self,
        timeout: int | None = None,
        line_length: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Markdownlint-specific options.

        Args:
            timeout: Timeout in seconds (default: 30).
            line_length: Line length for MD013 rule. If not provided, uses
                central line_length from [tool.lintro] or falls back to Ruff's
                line-length setting.
            **kwargs: Other tool options.
        """
        validate_positive_int(timeout, "timeout")

        set_kwargs = dict(kwargs)
        if timeout is not None:
            set_kwargs["timeout"] = timeout

        # Use provided line_length, or get from central config
        if line_length is None:
            line_length = get_central_line_length()

        validate_positive_int(line_length, "line_length")
        if line_length is not None:
            self.options["line_length"] = line_length

        super().set_options(**set_kwargs)

    def _get_markdownlint_command(self) -> list[str]:
        """Get the command to run markdownlint-cli2.

        Returns:
            Command arguments for markdownlint-cli2.
        """
        # Prefer direct executable if available (works better in Docker)
        if shutil.which("markdownlint-cli2"):
            return ["markdownlint-cli2"]
        # Fallback to bunx if direct executable not found
        if shutil.which("bunx"):
            return ["bunx", "markdownlint-cli2"]
        # Last resort - hope markdownlint-cli2 is in PATH
        return ["markdownlint-cli2"]

    def _create_temp_markdownlint_config(
        self,
        line_length: int,
    ) -> str | None:
        """Create a temporary markdownlint-cli2 config with the specified line length.

        Creates a temp file with MD013 rule configured. This avoids modifying
        the user's project files.

        Args:
            line_length: Line length to configure for MD013 rule.

        Returns:
            Path to the temporary config file, or None if creation failed.
        """
        config_wrapper: dict[str, object] = {
            "config": {
                "MD013": {
                    "line_length": line_length,
                    "code_blocks": False,
                    "tables": False,
                },
            },
        }

        try:
            # Create a temp file that persists until explicitly deleted
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".markdownlint-cli2.jsonc",
                prefix="lintro-",
                delete=False,
                encoding="utf-8",
            ) as f:
                json.dump(config_wrapper, f, indent=2)
                temp_path = f.name

            logger.debug(
                f"[MarkdownlintPlugin] Created temp config at {temp_path} "
                f"with line_length={line_length}",
            )
            return temp_path

        except (PermissionError, OSError) as e:
            logger.warning(
                f"[MarkdownlintPlugin] Could not create temp config file: {e}",
            )
            return None

    def doc_url(self, code: str) -> str | None:
        """Return markdownlint documentation URL for the given code.

        Args:
            code: Markdownlint code (e.g., "MD013").

        Returns:
            URL to the markdownlint rule documentation.
        """
        if code:
            return f"https://github.com/DavidAnson/markdownlint/blob/main/doc/{code.lower()}.md"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Markdownlint.

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
            f"[MarkdownlintPlugin] Discovered {len(ctx.files)} files matching "
            f"patterns: {self.definition.file_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[MarkdownlintPlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[MarkdownlintPlugin] Working directory: {ctx.cwd}")

        # Build command
        cmd: list[str] = self._get_markdownlint_command()

        # Track temp config for cleanup
        temp_config_path: str | None = None

        # Try Lintro config injection first
        config_args = self._build_config_args()
        if config_args:
            cmd.extend(config_args)
            logger.debug("[MarkdownlintPlugin] Using Lintro config injection")
        else:
            # Fallback: Apply line_length configuration if set
            line_length_opt = self.options.get("line_length")
            if line_length_opt is not None:
                line_length_val = (
                    int(line_length_opt)
                    if isinstance(line_length_opt, int)
                    else int(str(line_length_opt))
                )
                temp_config_path = self._create_temp_markdownlint_config(
                    line_length=line_length_val,
                )
                if temp_config_path:
                    cmd.extend(["--config", temp_config_path])

        cmd.extend(ctx.rel_files)

        logger.debug(
            f"[MarkdownlintPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})",
        )

        try:
            success, output = self._run_subprocess(
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
        finally:
            # Clean up temp config file if created
            if temp_config_path:
                try:
                    os.unlink(temp_config_path)
                    logger.debug(
                        "[MarkdownlintPlugin] Cleaned up temp config: "
                        f"{temp_config_path}",
                    )
                except OSError as e:
                    logger.debug(
                        f"[MarkdownlintPlugin] Failed to clean up temp config: {e}",
                    )

        # Parse output
        issues = parse_markdownlint_output(output=output)
        issues_count: int = len(issues)
        success_flag: bool = success and issues_count == 0

        # Suppress output when no issues found
        final_output: str | None = output
        if success_flag:
            final_output = None

        return ToolResult(
            name=self.definition.name,
            success=success_flag,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Markdownlint cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Markdownlint is a linter only and cannot fix issues.
        """
        raise NotImplementedError(
            "Markdownlint cannot fix issues; use a Markdown formatter"
            " for formatting.",
        )
