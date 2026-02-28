"""Ruff tool definition.

Ruff is an extremely fast Python linter and code formatter written in Rust.
It can replace multiple Python tools like flake8, black, isort, and more.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - subprocess used safely to resolve ruff rule names
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
    validate_positive_int,
    validate_str,
)
from lintro.utils.config import load_ruff_config
from lintro.utils.path_utils import load_lintro_ignore

# Constants for Ruff configuration
RUFF_DEFAULT_TIMEOUT: int = 30
RUFF_DEFAULT_PRIORITY: int = 85
RUFF_FILE_PATTERNS: list[str] = ["*.py", "*.pyi"]
RUFF_OUTPUT_FORMAT: str = "json"
RUFF_TEST_MODE_ENV: str = "LINTRO_TEST_MODE"
RUFF_TEST_MODE_VALUE: str = "1"


@register_tool
@dataclass
class RuffPlugin(BaseToolPlugin):
    """Ruff Python linter and formatter plugin.

    This plugin integrates Ruff with Lintro for linting and formatting
    Python files.
    """

    _rule_name_cache: dict[str, str | None] = field(
        default_factory=dict,
        repr=False,
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="ruff",
            description="Fast Python linter and formatter replacing multiple tools",
            can_fix=True,
            tool_type=ToolType.LINTER | ToolType.FORMATTER,
            file_patterns=RUFF_FILE_PATTERNS,
            priority=RUFF_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["pyproject.toml", "ruff.toml", ".ruff.toml"],
            version_command=["ruff", "--version"],
            min_version="0.14.0",
            default_options={
                "timeout": RUFF_DEFAULT_TIMEOUT,
                "select": None,
                "ignore": None,
                "extend_select": None,
                "extend_ignore": None,
                "line_length": None,
                "target_version": None,
                "fix_only": False,
                "unsafe_fixes": False,
                "show_fixes": False,
                "format_check": True,
                "format": True,
                "lint_fix": True,
            },
            default_timeout=RUFF_DEFAULT_TIMEOUT,
        )

    def __post_init__(self) -> None:
        """Initialize the tool with configuration from pyproject.toml."""
        super().__post_init__()

        # Skip config loading in test mode
        if os.environ.get(RUFF_TEST_MODE_ENV) != RUFF_TEST_MODE_VALUE:
            ruff_config = load_ruff_config()
            lintro_ignore_patterns = load_lintro_ignore()

            # Update exclude patterns
            if "exclude" in ruff_config:
                self.exclude_patterns.extend(ruff_config["exclude"])
            if lintro_ignore_patterns:
                self.exclude_patterns.extend(lintro_ignore_patterns)

            # Update options from configuration
            for key in (
                "line_length",
                "target_version",
                "select",
                "ignore",
                "unsafe_fixes",
            ):
                if key in ruff_config:
                    self.options[key] = ruff_config[key]

        # Allow environment variable override for unsafe fixes
        env_unsafe_fixes = os.environ.get("RUFF_UNSAFE_FIXES", "").lower()
        if env_unsafe_fixes in ("true", "1", "yes", "on"):
            self.options["unsafe_fixes"] = True

    def set_options(  # type: ignore[override]
        self,
        select: list[str] | None = None,
        ignore: list[str] | None = None,
        extend_select: list[str] | None = None,
        extend_ignore: list[str] | None = None,
        line_length: int | None = None,
        target_version: str | None = None,
        fix_only: bool | None = None,
        unsafe_fixes: bool | None = None,
        show_fixes: bool | None = None,
        format: bool | None = None,
        lint_fix: bool | None = None,
        format_check: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Ruff-specific options.

        Args:
            select: Rules to enable.
            ignore: Rules to ignore.
            extend_select: Additional rules to enable.
            extend_ignore: Additional rules to ignore.
            line_length: Line length limit.
            target_version: Python version target.
            fix_only: Only apply fixes, don't report remaining issues.
            unsafe_fixes: Include unsafe fixes.
            show_fixes: Show enumeration of fixes applied.
            format: Whether to run `ruff format` during fix.
            lint_fix: Whether to run `ruff check --fix` during fix.
            format_check: Whether to run `ruff format --check` in check.
            **kwargs: Other tool options.
        """
        # Normalize string-or-list parameters
        select = normalize_str_or_list(select, "select")
        ignore = normalize_str_or_list(ignore, "ignore")
        extend_select = normalize_str_or_list(extend_select, "extend_select")
        extend_ignore = normalize_str_or_list(extend_ignore, "extend_ignore")

        # Validate types
        validate_positive_int(line_length, "line_length")
        validate_str(target_version, "target_version")
        validate_bool(fix_only, "fix_only")
        validate_bool(unsafe_fixes, "unsafe_fixes")
        validate_bool(show_fixes, "show_fixes")
        validate_bool(format, "format")
        validate_bool(lint_fix, "lint_fix")
        validate_bool(format_check, "format_check")

        options = filter_none_options(
            select=select,
            ignore=ignore,
            extend_select=extend_select,
            extend_ignore=extend_ignore,
            line_length=line_length,
            target_version=target_version,
            fix_only=fix_only,
            unsafe_fixes=unsafe_fixes,
            show_fixes=show_fixes,
            format=format,
            lint_fix=lint_fix,
            format_check=format_check,
        )
        super().set_options(**options, **kwargs)

    def _resolve_rule_name(self, code: str) -> str | None:
        """Resolve a rule code to its slug via ``ruff rule``.

        Results are cached per-session so the CLI is invoked at most once
        per unique code.

        Args:
            code: Ruff rule code (e.g., "E501").

        Returns:
            Rule name slug (e.g., "line-too-long"), or None on failure.
        """
        if code in self._rule_name_cache:
            return self._rule_name_cache[code]

        try:
            cmd = self._get_executable_command(tool_name="ruff")
            cmd.extend(["rule", "--output-format", "json", code])
            result = subprocess.run(  # nosec B603
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                name: str | None = data.get("name")
                self._rule_name_cache[code] = name
                return name
        except (
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            OSError,
        ):
            logger.debug("Failed to resolve ruff rule name for {}", code)

        self._rule_name_cache[code] = None
        return None

    def doc_url(self, code: str) -> str | None:
        """Return Ruff documentation URL for the given rule code.

        Resolves the rule name slug via ``ruff rule`` so the URL points
        to the correct page (e.g., ``line-too-long`` instead of ``E501``).

        Args:
            code: Ruff rule code (e.g., "E501", "F401").

        Returns:
            URL to the Ruff rule documentation, or None if code is empty
            or the rule name cannot be resolved.
        """
        if not code:
            return None
        name = self._resolve_rule_name(code)
        if name:
            return f"https://docs.astral.sh/ruff/rules/{name}/"
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Ruff.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Apply runtime options to self.options before execution
        if options:
            self.options.update(options)

        from lintro.tools.implementations.ruff.check import execute_ruff_check

        return execute_ruff_check(self, paths)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files with Ruff.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Apply runtime options to self.options before execution
        if options:
            self.options.update(options)

        from lintro.tools.implementations.ruff.fix import execute_ruff_fix

        return execute_ruff_fix(self, paths)
