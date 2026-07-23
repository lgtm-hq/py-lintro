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

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.trufflehog.trufflehog_errors import (
    extract_trufflehog_scan_errors,
    scan_errors_are_all_benign,
)
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

# TruffleHog's ``filesystem`` mode takes explicit file paths in argv. A large
# repository can hold tens of thousands of files, and expanding them all into a
# single invocation would exceed the OS ``ARG_MAX`` limit, making ``execve``
# fail with ``E2BIG`` (surfaced as an ``OSError`` that fails the whole scan). We
# batch the paths under a byte budget derived from ``ARG_MAX`` with headroom for
# the environment block and the fixed command prefix.
_ARGV_SAFETY_HEADROOM_BYTES: int = 4096
_ARGV_FALLBACK_LIMIT_BYTES: int = 131072  # POSIX-guaranteed ARG_MAX minimum.


def _argv_byte_budget() -> int:
    """Return a safe byte budget for path arguments on one command line.

    The budget is derived from the OS ``ARG_MAX`` limit, reserving room for the
    current environment block (``execve`` counts it against the same limit) and
    a fixed safety margin. Falls back to the POSIX-guaranteed minimum when
    ``ARG_MAX`` cannot be queried.

    Returns:
        The maximum number of argument-data bytes to place on one command line.
    """
    try:
        arg_max = os.sysconf("SC_ARG_MAX")
    except (ValueError, OSError, AttributeError):
        arg_max = _ARGV_FALLBACK_LIMIT_BYTES
    if not isinstance(arg_max, int) or arg_max <= 0:
        arg_max = _ARGV_FALLBACK_LIMIT_BYTES

    env_bytes = sum(len(k) + len(v) + 2 for k, v in os.environ.items())
    budget = arg_max - env_bytes - _ARGV_SAFETY_HEADROOM_BYTES
    # Always leave room for at least a moderately long single path per batch.
    return max(budget, _ARGV_SAFETY_HEADROOM_BYTES)


def _existing_option_path(raw_path: str) -> str | None:
    """Return ``raw_path`` when it exists on disk, else ``None``.

    Used for optional TruffleHog file flags (currently ``--exclude-paths``) so
    CI-only paths that are absent locally are skipped rather than handed to the
    binary (which would emit ``lstat …: no such file or directory``).

    Args:
        raw_path: Configured filesystem path string.

    Returns:
        The path string when the file or directory exists, otherwise ``None``.
    """
    try:
        if Path(raw_path).exists():
            return raw_path
    except OSError:
        return None
    return None


def _chunk_source_paths(
    source_paths: list[str],
    *,
    fixed_arg_bytes: int,
) -> list[list[str]]:
    """Split resolved file paths into ARG_MAX-safe batches.

    Batches preserve input order so scan output is deterministic. A single path
    that alone exceeds the budget is still placed in its own batch (the OS, not
    lintro, then decides whether it is too long); this keeps the function total
    and never silently drops a file from the scan.

    Args:
        source_paths: Absolute file paths to scan, in a stable order.
        fixed_arg_bytes: Byte length of the non-path portion of the command
            (executable, subcommand, flags), which counts against the same
            OS limit.

    Returns:
        A list of path batches, each safe to place on one command line.
    """
    budget = max(_argv_byte_budget() - fixed_arg_bytes, 1)

    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for path in source_paths:
        # +1 for the argv NUL terminator the kernel accounts per argument.
        path_bytes = len(path.encode("utf-8", "surrogatepass")) + 1
        if current and current_bytes + path_bytes > budget:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(path)
        current_bytes += path_bytes
    if current:
        batches.append(current)
    return batches


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

        Raises:
            ValueError: If an explicitly configured TruffleHog config file is
                missing.
        """
        cmd: list[str] = ["trufflehog", "filesystem", *source_paths]

        # Machine-readable JSONL output on stdout.
        cmd.append("--json")

        # Never let trufflehog self-update at scan time: the binary is baked
        # into the (read-only) tools image, so the updater's "cannot move
        # binary" failure would surface as a spurious tool error. Also keeps
        # scans hermetic (no outbound update check).
        cmd.append("--no-update")

        # Disable network verification by default (hermetic scans).
        if self.options.get("no_verification", True):
            cmd.append("--no-verification")

        # Result-type filter
        results_opt = self.options.get("results")
        if results_opt is not None:
            cmd.extend(["--results", str(results_opt)])

        # Custom detector config — only when the file exists on disk so a
        # missing explicit config never downgrades the scan to default
        # detectors.
        config_opt = self.options.get("config")
        if isinstance(config_opt, str) and config_opt:
            config_path = _existing_option_path(config_opt)
            if config_path is not None:
                cmd.extend(["--config", config_path])
            else:
                raise ValueError(
                    "TruffleHog config file does not exist: "
                    f"{config_opt}. Refusing to run without the configured "
                    "custom detectors.",
                )

        # Exclude-paths file — same existence gate for CI-only exclude lists.
        exclude_opt = self.options.get("exclude_paths")
        if isinstance(exclude_opt, str) and exclude_opt:
            exclude_path = _existing_option_path(exclude_opt)
            if exclude_path is not None:
                cmd.extend(["--exclude-paths", exclude_path])
            else:
                logger.warning(
                    f"[trufflehog] Skipping absent --exclude-paths file: "
                    f"{exclude_opt}",
                )

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

        # Scan the discovered, filtered file set — never the raw CLI paths — so
        # that .lintro-ignore exclusions (test_samples/, .venv, build dirs, …)
        # and the venv filter are honored exactly like every other tool. When
        # filtering removes every file, ``_prepare_execution`` already returned
        # a no-files result above, so ``ctx.files`` is guaranteed non-empty here
        # and there is no unfiltered fallback that could reintroduce excluded
        # targets.
        #
        # Absolute paths are required: TruffleHog resolves relative paths
        # against its own working directory, and an unresolved path makes it
        # exit 0 with no output, which a secrets scanner must never treat as a
        # clean pass. A stable sort keeps batch boundaries and aggregated output
        # deterministic.
        source_paths = sorted(str(Path(f).resolve()) for f in ctx.files)
        if not source_paths:
            return ToolResult(
                name=self.definition.name,
                success=True,
                output="No files to check.",
                issues_count=0,
            )
        missing_scan_paths = [
            source_path
            for source_path in source_paths
            if not Path(source_path).exists()
        ]
        if missing_scan_paths:
            missing_list = "\n".join(f"  - {path}" for path in missing_scan_paths)
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    "TruffleHog scan incomplete: resolved scan target(s) "
                    "disappeared before execution.\n"
                    f"{missing_list}"
                ),
                issues_count=0,
                parse_failures_count=1,
            )

        # Expanding every file into one invocation can exceed ARG_MAX on large
        # repositories, so batch the paths under a byte budget and merge the
        # per-batch results into a single ToolResult.
        try:
            fixed_args = self._build_check_command(source_paths=[])
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to prepare TruffleHog command: {e}")
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=f"TruffleHog failed: {e}",
                issues_count=0,
            )
        fixed_arg_bytes = sum(
            len(a.encode("utf-8", "surrogatepass")) + 1 for a in fixed_args
        )
        batches = _chunk_source_paths(source_paths, fixed_arg_bytes=fixed_arg_bytes)
        logger.debug(
            f"[trufflehog] Scanning {len(source_paths)} files in "
            f"{len(batches)} batch(es) (cwd={ctx.cwd})",
        )

        all_issues: list[Any] = []
        failures: list[ToolResult] = []
        for batch in batches:
            batch_result = self._scan_batch(source_paths=batch, ctx=ctx)
            if batch_result.issues:
                all_issues.extend(batch_result.issues)
            if not batch_result.success:
                failures.append(batch_result)

        if failures:
            combined = "\n".join(f.output for f in failures if f.output)
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=combined or "TruffleHog failed with a non-zero exit code.",
                issues=all_issues,
                issues_count=len(all_issues),
                parse_failures_count=sum(
                    (f.parse_failures_count or 0) for f in failures
                ),
            )

        return ToolResult(
            name=self.definition.name,
            success=True,
            output=None,
            issues=all_issues,
            issues_count=len(all_issues),
            parse_failures_count=0,
        )

    def _scan_batch(
        self,
        *,
        source_paths: list[str],
        ctx: Any,
    ) -> ToolResult:
        """Scan one ARG_MAX-safe batch of files and interpret the result.

        Args:
            source_paths: Absolute file paths for this batch.
            ctx: The prepared execution context (supplies timeout and cwd).

        Returns:
            A ToolResult for the batch: ``success`` False on any execution,
            scan, or parse failure, with any emitted findings preserved.
        """
        # TruffleHog writes JSONL findings to stdout and diagnostic logs to
        # stderr, so parse stdout independently (see #1043).
        try:
            cmd = self._build_check_command(source_paths=source_paths)
            logger.debug(
                f"[trufflehog] Running: {' '.join(cmd[:10])}... (cwd={ctx.cwd})",
            )
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
        # "encountered errors during scan" to stderr). Fail closed on genuine
        # incomplete scans (#1044), but ignore benign ``lstat``/``stat``
        # missing-path errors for targets that were never part of the resolved
        # scan set — typically CI-only artifact dirs (#1631).
        if "encountered errors during scan" in stderr:
            scan_errors = extract_trufflehog_scan_errors(stderr)
            scan_path_set = frozenset(source_paths)
            if scan_errors_are_all_benign(scan_errors, scan_paths=scan_path_set):
                logger.warning(
                    "[trufflehog] Ignoring benign missing-path scan errors "
                    f"outside the resolved scan set: {scan_errors}",
                )
            else:
                logger.error(f"TruffleHog reported scan errors: {stderr[:500]}")
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
