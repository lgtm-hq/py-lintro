"""Spectral tool definition.

Spectral is a flexible JSON/YAML linter with first-class support for OpenAPI
(2.0/3.0/3.1), AsyncAPI, and JSON Schema documents. It is check-only (no
fixer) and requires a ruleset (``.spectral.yaml`` and friends). When no ruleset
is present, Spectral cannot run meaningfully, so lintro skips it gracefully
rather than reporting an error.
"""

from __future__ import annotations

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
from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.parsers.spectral.spectral_parser import (
    has_spectral_json_payload,
    parse_spectral_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
    validate_str,
)
from lintro.tools.core.timeout_utils import create_timeout_result
from lintro.utils.path_filtering import find_project_root
from lintro.utils.path_utils import find_file_upward
from lintro.utils.unified_config import DEFAULT_TOOL_PRIORITIES

# Constants for Spectral configuration
SPECTRAL_DEFAULT_TIMEOUT: int = 30
SPECTRAL_DEFAULT_PRIORITY: int = DEFAULT_TOOL_PRIORITIES.get("spectral", 45)
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
# Built-in Spectral 6.16 rule-code families that live on the OpenAPI rules page.
# Keep broad, generic prefixes such as ``no-`` and ``info-`` out: custom rules can
# legitimately use them and must not inherit a misleading OpenAPI link.
_SPECTRAL_OAS_PREFIXES: tuple[str, ...] = (
    "oas2-",
    "oas3-",
    "oas3_",
    "openapi-",
    "operation-",
)
_SPECTRAL_OAS_EXACT_CODES: frozenset[str] = frozenset(
    {
        "array-items",
        "contact-properties",
        "duplicated-entry-in-enum",
        "info-contact",
        "info-description",
        "info-license",
        "license-url",
        "no-$ref-siblings",
        "no-eval-in-markdown",
        "no-script-tags-in-markdown",
        "path-declarations-must-exist",
        "path-keys-no-trailing-slash",
        "path-not-include-query",
        "path-params",
        "tag-description",
        "typed-enum",
    },
)


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
            native_configs=list(SPECTRAL_RULESET_FILES),
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

    def _find_ruleset(
        self,
        search_dir: str | None = None,
        options: dict[str, object] | None = None,
        stop_dir: str | None = None,
    ) -> str | None:
        """Locate a Spectral ruleset.

        Uses an explicitly configured ruleset when provided; otherwise searches
        upward from the target directory for a ``.spectral.*`` file, matching
        Spectral's own default resolution.

        Args:
            search_dir: Directory to start searching from. Defaults to CWD.
            options: Effective options for this invocation. Defaults to the
                plugin's configured options.
            stop_dir: Highest directory discovery may inspect. Defaults to an
                unbounded upward search.

        Returns:
            Path to the ruleset if found, otherwise None.
        """
        effective_options = self.options if options is None else options
        ruleset = effective_options.get("ruleset")
        if ruleset:
            return str(ruleset)

        start_dir = Path(search_dir).absolute() if search_dir else Path.cwd()
        if start_dir.is_file():
            start_dir = start_dir.parent
        max_depth: int | None = None
        if stop_dir:
            boundary = Path(stop_dir).absolute()
            try:
                relative = start_dir.relative_to(boundary)
                max_depth = len(relative.parts) + 1
            except ValueError:
                max_depth = 1
        found = find_file_upward(
            start_dir,
            SPECTRAL_RULESET_FILES,
            max_depth=max_depth,
        )
        if found is not None:
            logger.debug(
                f"[SpectralPlugin] Found ruleset: {found} (searched from {start_dir})",
            )
            return str(found)
        return None

    def _get_spectral_command(self, cwd: str | Path | None = None) -> list[str]:
        """Get the command to run spectral.

        Uses the shared Node.js resolution chain (project-local binary, PATH,
        then pinned ``bunx``/``npx``).

        Args:
            cwd: Directory the tool will execute in, when known.

        Returns:
            Command argument list to invoke spectral.
        """
        return self._get_executable_command(tool_name="spectral", cwd=cwd)

    def doc_url(self, code: str) -> str | None:
        """Return the Spectral documentation URL for the given rule.

        Spectral 6.16 emits per-rule SARIF help URIs on a retired host that now
        returns 404. Recognized built-in OpenAPI, AsyncAPI, and Arazzo codes
        therefore link to the maintained rule files in Spectral's official
        repository. Custom and JSON Schema codes return None rather than
        implying a built-in mapping.

        Args:
            code: Spectral rule code (e.g., ``oas3-api-servers``).

        Returns:
            Per-rule documentation URL, or None when the code is empty or
            not a built-in OpenAPI/AsyncAPI/Arazzo rule.
        """
        if not code:
            return None
        lowered = code.lower()
        if lowered.startswith("asyncapi-"):
            return DocUrlTemplate.SPECTRAL_ASYNCAPI.format(code=lowered)
        if lowered.startswith("arazzo-"):
            return DocUrlTemplate.SPECTRAL_ARAZZO.format(code=lowered)
        if lowered in _SPECTRAL_OAS_EXACT_CODES or any(
            lowered.startswith(prefix) for prefix in _SPECTRAL_OAS_PREFIXES
        ):
            return DocUrlTemplate.SPECTRAL.format(code=lowered)
        return None

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

        # Spectral accepts one ruleset per process. Group matched files by the
        # nearest ruleset discovered from each file so nested project configs
        # cannot leak onto unrelated files. An explicit option intentionally
        # overrides discovery for the whole invocation.
        execution_root = ctx.cwd or str(Path.cwd())
        discovery_boundary = find_project_root(execution_root)
        ruleset_groups: dict[str, list[str]] = {}
        explicit_ruleset = merged_options.get("ruleset")
        if explicit_ruleset:
            ruleset_path = Path(str(explicit_ruleset)).expanduser()
            if not ruleset_path.is_absolute():
                ruleset_path = Path(execution_root) / ruleset_path
            ruleset_groups[str(ruleset_path.absolute())] = list(ctx.rel_files)
        else:
            for file_path, rel_file in zip(ctx.files, ctx.rel_files, strict=True):
                ruleset = self._find_ruleset(
                    search_dir=str(Path(file_path).parent),
                    options=merged_options,
                    stop_dir=discovery_boundary,
                )
                if ruleset:
                    ruleset_groups.setdefault(
                        str(Path(ruleset).absolute()),
                        [],
                    ).append(rel_file)

        if not ruleset_groups:
            ruleset_names = ", ".join(SPECTRAL_RULESET_FILES)
            logger.debug(
                "[SpectralPlugin] No ruleset found; skipping. Add a "
                ".spectral.yaml to enable Spectral.",
            )
            return ToolResult(
                name=self.definition.name,
                success=True,
                output=(
                    "Skipping spectral: no ruleset found. Add one of "
                    f"{ruleset_names} to enable API linting."
                ),
                issues_count=0,
                skipped=True,
                skip_reason="no ruleset found",
            )

        logger.debug(
            f"[SpectralPlugin] Discovered {len(ctx.files)} matching files in "
            f"{len(ruleset_groups)} ruleset group(s)",
        )
        if ctx.files:
            logger.debug(
                f"[SpectralPlugin] Files to check (first 10): {ctx.files[:10]}",
            )

        command_prefix = self._get_spectral_command(cwd=ctx.cwd)
        all_issues: list[SpectralIssue] = []
        finding_outputs: list[str] = []
        for ruleset, rel_files in ruleset_groups.items():
            cmd = [
                *command_prefix,
                "lint",
                "--format",
                "json",
                "--ignore-unknown-format",
                "--ruleset",
                ruleset,
                *rel_files,
            ]
            logger.debug(
                f"[SpectralPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})",
            )
            try:
                process = self._run_subprocess_result(
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
                    timed_out=timeout_result.timed_out,
                    output=timeout_result.output,
                    issues_count=timeout_result.issues_count,
                    cwd=ctx.cwd,
                )

            issues = parse_spectral_output(output=process.stdout)
            # Spectral exits 1 when findings exist. Any other failed process
            # with nothing parsed is a runtime failure, never a clean pass.
            if not process.success and not issues:
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=process.output
                    or "Spectral exited with an error and no results.",
                    issues_count=0,
                    cwd=ctx.cwd,
                )
            # Successful output must contain either findings or a decoded empty
            # array; malformed warning-only output fails closed.
            if (
                process.success
                and not issues
                and not has_spectral_json_payload(process.stdout)
            ):
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=process.output or "Spectral output could not be parsed.",
                    issues_count=0,
                    cwd=ctx.cwd,
                )
            all_issues.extend(issues)
            if issues and process.output:
                finding_outputs.append(process.output)

        for issue in all_issues:
            issue.doc_url = self.doc_url(issue.code) or ""
        issues_count = len(all_issues)
        success_flag: bool = issues_count == 0
        final_output = "\n".join(finding_outputs) or None

        return ToolResult(
            name=self.definition.name,
            success=success_flag,
            output=final_output,
            issues_count=issues_count,
            issues=all_issues,
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
