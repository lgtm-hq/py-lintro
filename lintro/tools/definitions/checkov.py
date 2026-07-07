"""Checkov tool definition.

Checkov is a static analysis tool for Infrastructure-as-Code (IaC) that detects
security and compliance misconfigurations. This plugin scopes Checkov to
Terraform sources and runs it in a hermetic, offline mode (no policy download,
no external module fetch, no result upload to any platform).
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.checkov.checkov_parser import parse_checkov_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_list,
)

# Constants for Checkov configuration
CHECKOV_DEFAULT_TIMEOUT: int = 120
CHECKOV_DEFAULT_PRIORITY: int = 88  # High priority for a security tool
# Scoped to Terraform only. See docs/tool-analysis/checkov-analysis.md for the
# rationale: Dockerfiles are intentionally left to hadolint (no double-report),
# and broad *.yaml/*.json globs would feed non-IaC files to Checkov.
CHECKOV_FILE_PATTERNS: list[str] = ["*.tf", "*.tf.json"]


def _extract_checkov_json(raw_text: str) -> Any:
    """Extract Checkov's JSON payload from stdout.

    ``checkov --output json`` writes a clean JSON document to stdout, but this
    helper is defensive against a leading ASCII banner by locating the first
    JSON delimiter (object or array) and the matching trailing delimiter.

    Args:
        raw_text: Combined stdout text from Checkov.

    Returns:
        Parsed JSON payload (object or array).

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed.
        ValueError: If no JSON delimiters are found.
    """
    if not raw_text or not raw_text.strip():
        raise json.JSONDecodeError("Empty output", raw_text or "", 0)

    text: str = raw_text.strip()
    if (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    ):
        return json.loads(text)

    obj_start = text.find("{")
    arr_start = text.find("[")
    candidates = [i for i in (obj_start, arr_start) if i != -1]
    if not candidates:
        raise ValueError("Could not locate JSON payload in Checkov output")
    start = min(candidates)
    end = max(text.rfind("}"), text.rfind("]"))
    if end < start:
        raise ValueError("Could not locate JSON payload in Checkov output")
    return json.loads(text[start : end + 1])


@register_tool
@dataclass
class CheckovPlugin(BaseToolPlugin):
    """Checkov Infrastructure-as-Code security scanner plugin.

    Integrates Checkov with lintro to detect security and compliance
    misconfigurations in Terraform sources.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="checkov",
            description=(
                "Infrastructure-as-Code security scanner for Terraform "
                "misconfigurations"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY | ToolType.INFRASTRUCTURE,
            file_patterns=CHECKOV_FILE_PATTERNS,
            priority=CHECKOV_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[".checkov.yaml", ".checkov.yml"],
            version_command=["checkov", "--version"],
            min_version=get_min_version(ToolName.CHECKOV),
            default_options={
                "timeout": CHECKOV_DEFAULT_TIMEOUT,
                "skip_checks": None,
                "checks": None,
                "compact": True,
                "skip_download": True,
            },
            default_timeout=CHECKOV_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        skip_checks: list[str] | None = None,
        checks: list[str] | None = None,
        compact: bool | None = None,
        skip_download: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Checkov-specific options.

        Args:
            skip_checks: Check IDs to skip (e.g., ``["CKV_AWS_18"]``).
            checks: Only run these check IDs.
            compact: Omit code blocks from output (smaller payload).
            skip_download: Skip downloading external policies (keeps runs
                offline). Disabling this would allow network access.
            **kwargs: Other tool options.
        """
        validate_list(skip_checks, "skip_checks")
        validate_list(checks, "checks")
        validate_bool(compact, "compact")
        validate_bool(skip_download, "skip_download")

        options = filter_none_options(
            skip_checks=skip_checks,
            checks=checks,
            compact=compact,
            skip_download=skip_download,
        )
        super().set_options(**options, **kwargs)

    def _build_check_command(self, files: list[str]) -> list[str]:
        """Build the checkov check command.

        Args:
            files: List of Terraform files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["checkov", "--output", "json"]

        if self.options.get("compact", True):
            cmd.append("--compact")

        # Hermetic defaults: never download external policies or modules, and
        # never upload results (no --bc-api-key is ever passed).
        if self.options.get("skip_download", True):
            cmd.append("--skip-download")
        cmd.extend(["--download-external-modules", "False"])

        checks_opt = self.options.get("checks")
        if isinstance(checks_opt, list) and checks_opt:
            cmd.extend(["--check", ",".join(str(c) for c in checks_opt)])

        skip_opt = self.options.get("skip_checks")
        if isinstance(skip_opt, list) and skip_opt:
            cmd.extend(["--skip-check", ",".join(str(c) for c in skip_opt)])

        # checkov's -f/--file appends a single path per flag; passing several
        # paths after one -f leaves the rest unattached (scanned never or
        # errored). Repeat the flag per file.
        for file_path in files:
            cmd.extend(["-f", file_path])
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return a documentation URL for the given check code.

        Checkov only emits per-check ``guideline`` URLs when run with a platform
        API key; in offline mode the parser leaves ``doc_url`` unset, so this
        fallback links to Checkov's policy index.

        Args:
            code: Checkov check ID (e.g., ``CKV_AWS_23``).

        Returns:
            URL to Checkov's policy index, or None if code is empty.
        """
        if code:
            return DocUrlTemplate.CHECKOV
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Terraform files with Checkov for misconfigurations.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        cmd = self._build_check_command(files=ctx.files)
        logger.debug(f"[checkov] Running: {' '.join(cmd[:8])}...")

        try:
            completed = subprocess.run(  # nosec B603 - cmd is a validated list
                cmd,
                capture_output=True,
                text=True,
                timeout=ctx.timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    f"Checkov execution timed out ({ctx.timeout}s limit exceeded). "
                    "Increase via --tool-options checkov:timeout=N."
                ),
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.error(f"Failed to run Checkov: {exc}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Checkov failed: {exc}",
                issues_count=0,
            )

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        if not stdout:
            # No JSON payload. A zero exit means a clean run; anything else is a
            # genuine failure that must not be reported as a pass (security tool).
            if completed.returncode == 0:
                return ToolResult(
                    name=self.definition.name,
                    success=True,
                    output="Checkov ran successfully and found no issues",
                    issues_count=0,
                )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=stderr or "Checkov failed with non-zero exit code",
                issues_count=0,
            )

        try:
            data = _extract_checkov_json(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(f"Failed to parse checkov output: {exc}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=stdout,
                issues_count=0,
                parse_failures_count=1,
            )

        issues = parse_checkov_output(json.dumps(data))
        return ToolResult(
            name=self.definition.name,
            success=len(issues) == 0,
            output=None,
            issues_count=len(issues),
            issues=issues,
            parse_failures_count=0,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Checkov cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Checkov does not support fixing issues.
        """
        raise NotImplementedError(
            "Checkov cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )
