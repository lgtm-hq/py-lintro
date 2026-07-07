"""Spectral tool definition.

Spectral is a flexible JSON/YAML linter with first-class support for OpenAPI
(2.0/3.0/3.1), AsyncAPI, and JSON Schema documents. It is check-only (no
fixer) and requires a ruleset (``.spectral.yaml`` and friends). When no ruleset
is present, Spectral cannot run meaningfully, so lintro skips it gracefully
rather than reporting an error.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.spectral.spectral_parser import parse_spectral_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
    validate_str,
)
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.path_utils import find_file_upward

# Constants for Spectral configuration
SPECTRAL_DEFAULT_TIMEOUT: int = 30
SPECTRAL_DEFAULT_PRIORITY: int = 45
# Spectral targets structured API documents (OpenAPI/AsyncAPI/JSON Schema),
# which are authored as YAML or JSON. It only runs when a ruleset is present
# (see _find_ruleset), so these patterns do not cause every YAML/JSON file in a
# repository to be linted unless the project opts in with a ruleset.
SPECTRAL_FILE_PATTERNS: list[str] = ["*.yaml", "*.yml", "*.json"]
# Ruleset filenames Spectral discovers by default (mirrors its own resolution).
SPECTRAL_RULESET_FILES: list[str] = [
    ".spectral.yaml",
    ".spectral.yml",
    ".spectral.json",
    ".spectral.js",
]


@register_tool
@dataclass
class SpectralPlugin(BaseToolPlugin):
    """Spectral OpenAPI/AsyncAPI/JSON Schema linter plugin.

    Integrates Spectral with lintro for linting structured API documents. The
    plugin is check-only and requires a ruleset to run; without one it skips
    gracefully (mirroring lintro's handling of other ruleset-gated tools).
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="spectral",
            description=(
                "OpenAPI/AsyncAPI/JSON Schema linter for API design best practices"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=SPECTRAL_FILE_PATTERNS,
            priority=SPECTRAL_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[
                ".spectral.yaml",
                ".spectral.yml",
                ".spectral.json",
                ".spectral.js",
            ],
            version_command=["spectral", "--version"],
            min_version=get_min_version(ToolName.SPECTRAL),
            default_options={
                "timeout": SPECTRAL_DEFAULT_TIMEOUT,
                "ruleset": None,
            },
            default_timeout=SPECTRAL_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        ruleset: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Spectral-specific options.

        Args:
            timeout: Timeout in seconds (default: 30).
            ruleset: Explicit path to a Spectral ruleset. When omitted, the
                plugin discovers ``.spectral.*`` upward from the target.
            **kwargs: Other tool options.
        """
        validate_positive_int(timeout, "timeout")
        validate_str(ruleset, "ruleset")

        options = filter_none_options(
            timeout=timeout,
            ruleset=ruleset,
        )
        super().set_options(**options, **kwargs)

    def _find_ruleset(self, search_dir: str | None = None) -> str | None:
        """Locate a Spectral ruleset.

        Uses an explicitly configured ruleset when provided; otherwise searches
        upward from the target directory for a ``.spectral.*`` file, matching
        Spectral's own default resolution.

        Args:
            search_dir: Directory to start searching from. Defaults to CWD.

        Returns:
            Path to the ruleset if found, otherwise None.
        """
        ruleset = self.options.get("ruleset")
        if ruleset:
            return str(ruleset)

        start_dir = Path(search_dir).absolute() if search_dir else Path.cwd()
        found = find_file_upward(start_dir, SPECTRAL_RULESET_FILES)
        if found is not None:
            logger.debug(
                f"[SpectralPlugin] Found ruleset: {found} (searched from {start_dir})",
            )
            return str(found)
        return None

    def _get_spectral_command(self) -> list[str]:
        """Get the command to run spectral.

        The npm package (``@stoplight/spectral-cli``) installs a ``spectral``
        binary. A globally installed binary is preferred (Docker/Homebrew),
        falling back to running the full package name via ``bunx``/``npx`` so
        resolution works even when the binary is not on PATH.

        Returns:
            Command argument list to invoke spectral.
        """
        if shutil.which("spectral"):
            return ["spectral"]
        if shutil.which("bunx"):
            return ["bunx", "@stoplight/spectral-cli"]
        if shutil.which("npx"):
            return ["npx", "--yes", "@stoplight/spectral-cli"]
        return ["spectral"]

    def doc_url(self, code: str) -> str | None:
        """Return the Spectral documentation URL for the given rule.

        Rule codes are ruleset-defined (custom rulesets choose their own),
        so a single canonical documentation page is returned for all rules.

        Args:
            code: Spectral rule code (e.g., ``oas3-api-servers``).

        Returns:
            URL to the Spectral rules documentation.
        """
        return str(DocUrlTemplate.SPECTRAL)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with Spectral.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        merged_options = dict(self.options)
        merged_options.update(options)

        ctx = self._prepare_execution(
            paths,
            merged_options,
            no_files_message="No files to check.",
        )
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Spectral requires a ruleset. Without one it cannot lint, so skip
        # gracefully instead of surfacing an error (stylelint/vale pattern).
        ruleset = self._find_ruleset(search_dir=paths[0] if paths else None)
        if not ruleset:
            logger.debug(
                "[SpectralPlugin] No ruleset found; skipping. Add a "
                ".spectral.yaml to enable Spectral.",
            )
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=(
                    "Skipping spectral: no ruleset found. Add a .spectral.yaml "
                    "(or .spectral.yml/.spectral.json) to enable API linting."
                ),
                issues_count=0,
            )

        logger.debug(
            f"[SpectralPlugin] Discovered {len(ctx.files)} files matching "
            f"patterns: {self.definition.file_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[SpectralPlugin] Files to check (first 10): {ctx.files[:10]}",
            )

        cmd: list[str] = self._get_spectral_command() + [
            "lint",
            "--format",
            "json",
            "--ruleset",
            str(Path(ruleset).absolute()),
        ]
        cmd.extend(ctx.rel_files)
        logger.debug(f"[SpectralPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")

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

        issues = parse_spectral_output(output=output)

        # Spectral exits 1 when findings exist (which produce parseable JSON).
        # A non-zero exit with nothing parsed is a runtime failure (invalid
        # ruleset, missing runtime) — never report that as a clean pass.
        if not success and not issues:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=output or "Spectral exited with an error and no results.",
                issues_count=0,
                cwd=ctx.cwd,
            )

        for issue in issues:
            issue.doc_url = self.doc_url(issue.code) or ""
        issues_count: int = len(issues)
        success_flag: bool = issues_count == 0

        final_output: str | None = output
        if success_flag:
            final_output = None

        return ToolResult(
            name=self.definition.name,
            success=success_flag,
            output=final_output,
            issues_count=issues_count,
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Spectral cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths (unused).
            options: Runtime options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Spectral is a linter only and cannot fix issues.
        """
        raise NotImplementedError(
            "Spectral cannot fix issues; it is a linter for API documents.",
        )
