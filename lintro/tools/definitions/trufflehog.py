"""TruffleHog tool definition.

TruffleHog is a secrets-scanning tool that detects credentials (API keys,
tokens, private keys, and 800+ other credential types) using regex patterns and
entropy analysis. It optionally verifies whether detected credentials are live
by calling the corresponding provider APIs.

Lintro runs TruffleHog in ``filesystem`` mode with verification disabled
(``--no-verification``) so scans never make outbound network calls to third
parties. This mirrors lintro's file-oriented model and keeps runs hermetic and
deterministic. TruffleHog complements gitleaks: gitleaks is fast and
config-driven, while TruffleHog ships a large set of provider-specific detectors
and richer per-detector metadata, so the two catch overlapping-but-different
secrets.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_bool,
    validate_positive_int,
    validate_str,
)

# Constants for TruffleHog configuration
TRUFFLEHOG_DEFAULT_TIMEOUT: int = 60
TRUFFLEHOG_DEFAULT_PRIORITY: int = 90  # High priority for security tool
TRUFFLEHOG_FILE_PATTERNS: list[str] = ["*"]  # Scans all files


@register_tool
@dataclass
class TrufflehogPlugin(BaseToolPlugin):
    """TruffleHog secret detection plugin.

    This plugin integrates TruffleHog with Lintro for detecting hardcoded
    secrets such as API keys, tokens, and private keys in source code. It runs
    in filesystem mode with verification disabled by default so scans stay
    hermetic (no network calls).
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="trufflehog",
            description=(
                "Secrets scanner detecting API keys, tokens, and 800+ "
                "credential types via regex and entropy analysis"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=TRUFFLEHOG_FILE_PATTERNS,
            priority=TRUFFLEHOG_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=["trufflehog", "--version"],
            min_version=get_min_version(ToolName.TRUFFLEHOG),
            default_options={
                "timeout": TRUFFLEHOG_DEFAULT_TIMEOUT,
                # Disable live credential verification by default: lintro runs
                # must not make network calls to verify secrets.
                "no_verification": True,
                "results": None,
                "config": None,
                "exclude_paths": None,
                "concurrency": None,
            },
            default_timeout=TRUFFLEHOG_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        no_verification: bool | None = None,
        results: str | None = None,
        config: str | None = None,
        exclude_paths: str | None = None,
        concurrency: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set TruffleHog-specific options.

        Args:
            no_verification: Disable live verification of detected credentials.
                Defaults to True; setting this False enables outbound network
                calls to providers and is not recommended for CI.
            results: Comma-separated result types to output (e.g.
                "verified,unverified,unknown").
            config: Path to a TruffleHog configuration file (custom detectors).
            exclude_paths: Path to a file with newline-separated regexes for
                files to exclude from the scan.
            concurrency: Number of concurrent workers.
            **kwargs: Other tool options.
        """
        validate_bool(value=no_verification, name="no_verification")
        validate_str(value=results, name="results")
        validate_str(value=config, name="config")
        validate_str(value=exclude_paths, name="exclude_paths")
        validate_positive_int(value=concurrency, name="concurrency")

        options = filter_none_options(
            no_verification=no_verification,
            results=results,
            config=config,
            exclude_paths=exclude_paths,
            concurrency=concurrency,
        )
        super().set_options(**options, **kwargs)

    def _build_check_command(self, source_paths: list[str]) -> list[str]:
        """Build the trufflehog filesystem check command.

        Args:
            source_paths: Absolute paths to the files or directories to scan.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = ["trufflehog", "filesystem", *source_paths]

        # Machine-readable JSONL output on stdout.
        cmd.append("--json")

        # Disable network verification by default (hermetic scans).
        if self.options.get("no_verification", True):
            cmd.append("--no-verification")

        # Result-type filter
        results_opt = self.options.get("results")
        if results_opt is not None:
            cmd.extend(["--results", str(results_opt)])

        # Custom detector config
        config_opt = self.options.get("config")
        if config_opt is not None:
            cmd.extend(["--config", str(config_opt)])

        # Exclude-paths file
        exclude_opt = self.options.get("exclude_paths")
        if exclude_opt is not None:
            cmd.extend(["--exclude-paths", str(exclude_opt)])

        # Concurrency
        concurrency_opt = self.options.get("concurrency")
        if concurrency_opt is not None:
            cmd.extend(["--concurrency", str(concurrency_opt)])

        return cmd

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with TruffleHog for hardcoded secrets.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check and path validation.
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        # Resolve every provided path to an absolute path. TruffleHog resolves
        # relative paths against its own working directory, which differs from
        # the caller's cwd; an unresolved path makes TruffleHog exit 0 with no
        # output, which a security scanner must never treat as a clean pass.
        # TruffleHog's filesystem mode accepts multiple explicit paths, so scan
        # exactly what was requested rather than a broad common parent.
        if paths:
            source_paths = [str(Path(p).resolve()) for p in paths]
        else:
            source_paths = [str(Path.cwd())]

        cmd = self._build_check_command(source_paths=source_paths)
        logger.debug(
            f"[trufflehog] Running: {' '.join(cmd[:10])}... (cwd={ctx.cwd})",
        )

        # TruffleHog writes JSONL findings to stdout and diagnostic logs to
        # stderr, so parse stdout independently (see #1043).
        try:
            result = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            timeout_msg = (
                f"TruffleHog execution timed out ({ctx.timeout}s limit exceeded)."
                "\n\nThis may indicate:\n"
                "  - Large codebase taking too long to scan\n"
                "  - Need to increase timeout via "
                "--tool-options trufflehog:timeout=N"
            )
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=timeout_msg,
                issues_count=0,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to run TruffleHog: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"TruffleHog failed: {e}",
                issues_count=0,
            )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stderr:
            logger.debug(f"[trufflehog] stderr: {stderr[:500]}")

        # A non-zero exit is a genuine execution failure. TruffleHog exits 0
        # for both clean scans and findings (we do not pass --fail), so a
        # non-zero code always signals a real problem — even when partial
        # findings were emitted before the crash, the scan is incomplete and
        # must not read as a completed check. Parsed findings are preserved.
        if result.returncode != 0:
            partial = parse_trufflehog_output(output=stdout) if stdout else []
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=stderr or "TruffleHog failed with a non-zero exit code.",
                issues=partial,
                issues_count=len(partial),
                parse_failures_count=1,
            )

        # TruffleHog exits 0 even when it fails to read a scan target (it logs
        # "encountered errors during scan" to stderr). A security scanner must
        # never report a clean or complete pass from a scan that did not fully
        # run — even if other targets produced findings — so surface this as a
        # failure while keeping any findings that were emitted (see #1044).
        if "encountered errors during scan" in stderr:
            logger.error("TruffleHog reported scan errors: %s", stderr[:500])
            partial = parse_trufflehog_output(output=stdout) if stdout else []
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    "TruffleHog encountered errors during the scan; "
                    "treating as a failure rather than a clean pass."
                ),
                issues=partial,
                issues_count=len(partial),
                parse_failures_count=1,
            )

        issues = parse_trufflehog_output(output=stdout)
        issues_count = len(issues)

        # A security scanner must never report a clean pass from output it could
        # not parse. If stdout is non-empty but yielded zero findings, treat the
        # unparseable content as a failure (mirrors gitleaks; see #1044).
        if issues_count == 0 and stdout:
            logger.error("TruffleHog produced output that yielded no findings")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    "TruffleHog produced unparseable output; "
                    "treating as a parse failure."
                ),
                issues_count=0,
                parse_failures_count=1,
            )

        return ToolResult(
            name=self.definition.name,
            success=True,
            output=None,
            issues_count=issues_count,
            issues=issues,
            parse_failures_count=0,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """TruffleHog cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: TruffleHog does not support fixing issues.
        """
        raise NotImplementedError(
            "TruffleHog cannot automatically fix security issues. Run "
            "'lintro check' to see issues and manually remove or rotate the "
            "detected secrets.",
        )
