"""Trivy tool definition.

Trivy is a comprehensive security scanner by Aqua Security. This plugin scopes
Trivy to *filesystem dependency-vulnerability* scanning of lockfiles and
manifests (``trivy fs --scanners vuln``). Secret scanning is intentionally left
to gitleaks, and Infrastructure-as-Code misconfiguration scanning to checkov, so
Trivy does not double-report findings already owned by another lintro tool.

Trivy needs a local vulnerability database. To keep normal ``lintro`` runs
hermetic, the plugin runs with ``--skip-db-update`` (never downloads during a
run) and ``--offline-scan`` (never calls external advisory APIs) by default, and
bounds every invocation with a timeout. When the database is absent the plugin
reports a clear, non-fatal skip rather than hanging on a large download.
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
from lintro.parsers.trivy.trivy_parser import parse_trivy_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
)

# Constants for Trivy configuration
TRIVY_DEFAULT_TIMEOUT: int = 300  # Generous: a first DB download can be slow.
TRIVY_DEFAULT_PRIORITY: int = 87  # High priority for a security tool.

# Scoped to dependency lockfiles / manifests only. Trivy detects the ecosystem
# from the filename, so lintro feeds it only files it can scan for vulnerable
# dependencies. Secrets stay with gitleaks; IaC misconfig stays with checkov.
TRIVY_FILE_PATTERNS: list[str] = [
    "requirements.txt",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "go.mod",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "gradle.lockfile",
    "pom.xml",
]

# Substrings that identify a "vulnerability database is missing" error emitted
# when Trivy runs with --skip-db-update but has no cached DB. Matched
# case-insensitively against stderr.
_DB_MISSING_MARKERS: tuple[str, ...] = (
    "--skip-db-update",
    "skip-db-update",
    "needs to be updated",
    "database is not built",
    "please run trivy",
)


@register_tool
@dataclass
class TrivyPlugin(BaseToolPlugin):
    """Trivy dependency-vulnerability scanner plugin.

    Integrates Trivy with lintro to detect known vulnerabilities in dependency
    lockfiles and manifests, running hermetically by default.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="trivy",
            description=(
                "Dependency-vulnerability scanner for lockfiles and manifests"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=TRIVY_FILE_PATTERNS,
            priority=TRIVY_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=["trivy.yaml", ".trivy.yaml"],
            version_command=["trivy", "--version"],
            min_version=get_min_version(ToolName.TRIVY),
            default_options={
                "timeout": TRIVY_DEFAULT_TIMEOUT,
                "severity": None,
                "ignore_unfixed": False,
                "skip_db_update": True,
                "offline_scan": True,
            },
            default_timeout=TRIVY_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        severity: str | list[str] | None = None,
        ignore_unfixed: bool | None = None,
        skip_db_update: bool | None = None,
        offline_scan: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Trivy-specific options.

        Args:
            severity: Restrict findings to these severities (e.g.
                ``["CRITICAL", "HIGH"]`` or the pipe-delimited CLI form
                ``"CRITICAL|HIGH"``). ``None`` reports all severities.
            ignore_unfixed: When True, only report vulnerabilities that have a
                fixed version available.
            skip_db_update: When True (default), never download the
                vulnerability DB during a run (hermetic). Disabling this allows
                a one-time network download.
            offline_scan: When True (default), never call external advisory
                APIs during a scan.
            **kwargs: Other tool options.
        """
        severity_list = normalize_str_or_list(severity, "severity")
        validate_bool(ignore_unfixed, "ignore_unfixed")
        validate_bool(skip_db_update, "skip_db_update")
        validate_bool(offline_scan, "offline_scan")

        options = filter_none_options(
            severity=severity_list,
            ignore_unfixed=ignore_unfixed,
            skip_db_update=skip_db_update,
            offline_scan=offline_scan,
        )
        super().set_options(**options, **kwargs)

    def _build_check_command(self, file: str) -> list[str]:
        """Build the ``trivy fs`` command for a single target.

        Trivy accepts exactly one target path per invocation, so the plugin
        scans one lockfile per command.

        Args:
            file: Path to the lockfile / manifest to scan.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = [
            "trivy",
            "fs",
            "--scanners",
            "vuln",
            "--format",
            "json",
            "--quiet",
        ]

        # Hermetic defaults: never download the DB or call external APIs.
        if self.options.get("skip_db_update", True):
            cmd.append("--skip-db-update")
        if self.options.get("offline_scan", True):
            cmd.append("--offline-scan")

        if self.options.get("ignore_unfixed", False):
            cmd.append("--ignore-unfixed")

        severity = self.options.get("severity")
        if isinstance(severity, list) and severity:
            cmd.extend(["--severity", ",".join(str(s) for s in severity)])

        cmd.append(file)
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return a documentation URL for the given vulnerability code.

        The parser already captures Trivy's ``PrimaryURL`` per finding; this is
        the fallback used when that field is absent.

        Args:
            code: Vulnerability identifier (e.g. ``CVE-2019-14234``).

        Returns:
            URL to the advisory database entry, or None if code is empty.
        """
        if code:
            return DocUrlTemplate.TRIVY.format(code=code.lower())
        return None

    @staticmethod
    def _is_db_missing_error(stderr: str) -> bool:
        """Detect Trivy's "vulnerability DB not present" error.

        Args:
            stderr: Combined stderr text from a failed Trivy run.

        Returns:
            True when the failure is a missing local vulnerability database.
        """
        lowered = stderr.lower()
        return any(marker in lowered for marker in _DB_MISSING_MARKERS)

    def _db_missing_result(self) -> ToolResult:
        """Build the non-fatal result returned when the DB is unavailable.

        Returns:
            ToolResult: A successful (non-blocking) result explaining how to
                populate the vulnerability database.
        """
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=(
                "Trivy skipped: local vulnerability database not found. Populate "
                "it once with 'trivy fs --download-db-only' (requires network), "
                "or run lintro with "
                "--tool-options trivy:skip_db_update=false for a one-time "
                "download. Runs are hermetic (--skip-db-update) by default."
            ),
            issues_count=0,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check lockfiles / manifests with Trivy for known vulnerabilities.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        all_issues = []
        parse_failures = 0

        for file in ctx.files:
            cmd = self._build_check_command(file=file)
            logger.debug(f"[trivy] Running: {' '.join(cmd)}")

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
                        f"Trivy execution timed out ({ctx.timeout}s limit "
                        "exceeded). This usually means a vulnerability DB "
                        "download was triggered; pre-populate the DB with "
                        "'trivy fs --download-db-only', or increase the timeout "
                        "via --tool-options trivy:timeout=N."
                    ),
                    issues_count=0,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                logger.error(f"Failed to run Trivy: {exc}")
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=f"Trivy failed: {exc}",
                    issues_count=0,
                )

            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()

            if completed.returncode != 0:
                # A missing DB is an environment condition, not a scan failure:
                # report a clear, non-blocking skip instead of failing closed.
                # But never let a mid-loop skip hide vulnerabilities already
                # collected from earlier lockfiles.
                if self._is_db_missing_error(stderr):
                    if not all_issues:
                        return self._db_missing_result()
                    return ToolResult(
                        name=self.definition.name,
                        success=False,
                        output=(
                            "Trivy vulnerability DB became unavailable part-way "
                            "through the scan; reporting the findings collected "
                            "before the failure."
                        ),
                        issues_count=len(all_issues),
                        issues=all_issues,
                    )
                logger.error(f"Trivy failed on {file}: {stderr}")
                return ToolResult(
                    name=self.definition.name,
                    success=False,
                    output=stderr or "Trivy failed with non-zero exit code",
                    issues_count=len(all_issues),
                    issues=all_issues,
                )

            if not stdout:
                # Zero exit with no JSON means a clean scan of this file.
                continue

            try:
                json.loads(stdout)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(f"Failed to parse trivy output for {file}: {exc}")
                parse_failures += 1
                continue

            all_issues.extend(parse_trivy_output(stdout))

        if parse_failures and not all_issues:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output="Trivy produced unparseable output",
                issues_count=0,
                parse_failures_count=parse_failures,
            )

        return ToolResult(
            name=self.definition.name,
            success=len(all_issues) == 0,
            output=None,
            issues_count=len(all_issues),
            issues=all_issues,
            parse_failures_count=parse_failures,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Trivy cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Trivy does not support fixing issues.
        """
        raise NotImplementedError(
            "Trivy cannot automatically fix vulnerabilities. Run 'lintro check' "
            "to see issues, then upgrade the affected dependencies.",
        )
