"""Vue-tsc tool definition.

Vue-tsc is the TypeScript type checker for Vue Single File Components (SFCs).
This enables proper type checking for `.vue` files that regular `tsc` cannot
handle.

Example:
    # Check Vue project
    lintro check src/ --tools vue-tsc

    # With specific config
    lintro check src/ --tools vue-tsc --tool-options "vue-tsc:project=tsconfig.app.json"

Most of the orchestration (command construction, tsconfig discovery, single- and
multi-project execution, output shaping) lives in the shared
:class:`lintro.tools.definitions._ts_checker_base.TypeScriptCheckerPlugin` base.
This module supplies the vue-tsc-specific deltas: the binary command, `.vue`
file targeting, vue-tsc output parsing, tsconfig.app.json discovery, and
error-message copy.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, ClassVar

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.parsers.vue_tsc.vue_tsc_parser import (
    categorize_vue_tsc_issues,
    extract_missing_modules,
    parse_vue_tsc_output,
)
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.definitions._ts_checker_base import TypeScriptCheckerPlugin

# Constants for Vue-tsc configuration
VUE_TSC_DEFAULT_TIMEOUT: int = 120
VUE_TSC_DEFAULT_PRIORITY: int = 83  # After tsc (82)
VUE_TSC_FILE_PATTERNS: list[str] = ["*.vue"]


@register_tool
@dataclass
class VueTscPlugin(TypeScriptCheckerPlugin):
    """Vue-tsc type checking plugin.

    This plugin integrates vue-tsc with Lintro for static type checking
    of Vue Single File Components.
    """

    _tool_label: ClassVar[str] = "vue-tsc"
    _file_kind: ClassVar[str] = "Vue"
    _no_files_message: ClassVar[str] = "No Vue files to check."
    _temp_config_prefix: ClassVar[str] = ".lintro-vue-tsc-"
    _fix_error_message: ClassVar[str] = (
        "vue-tsc cannot automatically fix issues. Type errors require "
        "manual code changes."
    )
    # tsconfig.app.json takes priority for Vite Vue projects.
    _tsconfig_candidates: ClassVar[tuple[str, ...]] = (
        "tsconfig.app.json",
        "tsconfig.json",
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="vue-tsc",
            description="Vue TypeScript type checker for Vue SFC diagnostics",
            can_fix=False,
            tool_type=ToolType.LINTER | ToolType.TYPE_CHECKER,
            file_patterns=VUE_TSC_FILE_PATTERNS,
            priority=VUE_TSC_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["tsconfig.json", "tsconfig.app.json"],
            version_command=self._vue_tsc_cmd + ["--version"],
            min_version=get_min_version(ToolName.VUE_TSC),
            default_options={
                "timeout": VUE_TSC_DEFAULT_TIMEOUT,
                "project": None,
                "strict": None,
                "skip_lib_check": True,
                "use_project_files": False,
            },
            default_timeout=VUE_TSC_DEFAULT_TIMEOUT,
        )

    @functools.cached_property
    def _vue_tsc_cmd(self) -> list[str]:
        """Get the command to run vue-tsc.

        Prefers direct vue-tsc executable, falls back to bunx/npx.
        The result is cached so that repeated accesses (e.g. from the
        ``definition`` property and ``_build_command``) reuse the stored
        command without repeated ``shutil.which()`` lookups.

        Returns:
            Command arguments for vue-tsc.
        """
        return self._resolve_binary_command("vue-tsc")

    def _command_prefix(self) -> list[str]:
        """Return the vue-tsc command prefix.

        Returns:
            Command argument list for vue-tsc.
        """
        return list(self._vue_tsc_cmd)

    def _parse_output(self, output: str) -> list[Any]:
        """Parse raw vue-tsc output into structured issues.

        Args:
            output: Raw stdout/stderr text from vue-tsc.

        Returns:
            List of parsed vue-tsc issue objects.
        """
        return parse_vue_tsc_output(output=output)

    def _categorize_issues(
        self,
        issues: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        """Split vue-tsc issues into (type errors, dependency errors).

        Args:
            issues: Parsed vue-tsc issue objects.

        Returns:
            A ``(type_errors, dependency_errors)`` tuple.
        """
        return categorize_vue_tsc_issues(issues)

    def _extract_missing_modules(self, dependency_errors: list[Any]) -> list[str]:
        """Extract missing module names from vue-tsc dependency errors.

        Args:
            dependency_errors: Dependency-related vue-tsc issue objects.

        Returns:
            List of missing module names.
        """
        return extract_missing_modules(dependency_errors)

    def _not_found_output(self, error: FileNotFoundError) -> str:
        """Build guidance shown when the vue-tsc binary is not found.

        Args:
            error: The FileNotFoundError raised while launching vue-tsc.

        Returns:
            User-facing guidance text.
        """
        return (
            f"vue-tsc not found: {error}\n\n"
            "Please ensure vue-tsc is installed:\n"
            "  - Run 'bun add -D vue-tsc' or 'npm install -D vue-tsc'\n"
            "  - Or install globally: 'bun add -g vue-tsc'"
        )

    def _config_error_output(self, normalized_output: str) -> str:
        """Build guidance shown for a likely dependency/config error.

        Args:
            normalized_output: ANSI-stripped vue-tsc output.

        Returns:
            User-facing guidance text.
        """
        return (
            f"vue-tsc configuration error:\n{normalized_output}\n\n"
            "This usually means dependencies aren't installed.\n"
            "Suggestions:\n"
            "  - Run 'bun install' or 'npm install' in your project\n"
            "  - Use '--auto-install' flag to auto-install dependencies\n"
            "  - If using Docker, ensure node_modules is available"
        )
