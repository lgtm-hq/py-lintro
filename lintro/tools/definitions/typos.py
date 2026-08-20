"""Typos tool definition.

typos is a source-code spell checker written in Rust. It finds and corrects
misspellings in code and documentation with a very low false-positive rate,
understanding programming conventions (identifiers, escape sequences, etc.).
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
from lintro.parsers.typos.typos_issue import TyposIssue
from lintro.parsers.typos.typos_parser import parse_typos_report
from lintro.plugins.base import BaseToolPlugin, ExecutionContext
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.argv_batching import argv_cost, chunk_paths

# Constants for typos configuration
TYPOS_DEFAULT_TIMEOUT: int = 30
TYPOS_DEFAULT_PRIORITY: int = 50
# typos inspects text of any kind, so a catch-all pattern is appropriate here.
# Binary files are filtered out by :meth:`TyposPlugin._text_files` before the
# command line is built (typos itself only auto-detects binary content for
# files it discovers, not for paths passed as arguments).
TYPOS_FILE_PATTERNS: list[str] = ["*"]
TYPOS_DEFAULT_FORMAT: str = "json"
TYPOS_CONFIG_FILENAMES: list[str] = ["typos.toml", ".typos.toml", "_typos.toml"]
# typos exits 2 when it reported typos. Any other non-zero is a runtime
# failure (bad config, IO, usage), even if some JSON findings were emitted.
TYPOS_ISSUES_EXIT_CODE: int = 2
# Suffixes that are binary even when the first 8 KiB has no NUL (JPEG, PDF,
# ZIP, …). Discovery uses ``file_patterns=["*"]``, so format would otherwise
# pass these to ``--write-changes``.
BINARY_PATH_SUFFIXES: frozenset[str] = frozenset(
    {
        ".7z",
        ".a",
        ".bin",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".o",
        ".otf",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".sqlite",
        ".ttf",
        ".wasm",
        ".webm",
        ".webp",
        ".whl",
        ".woff",
        ".woff2",
        ".zip",
    },
)
# Bytes sampled from the head of each file when sniffing for binary content.
BINARY_SNIFF_BYTES: int = 8192
# Appended when a fix run fails after ``--write-changes`` has already executed:
# files on disk may be fully or partially corrected even though the counts
# below could not be verified.
_AFTER_WRITE_NOTE: str = (
    "Note: typos --write-changes already ran, so files may have been "
    "corrected on disk. The fixed/remaining counts below could not be "
    "verified and are reported conservatively; re-run "
    "`lintro check --tools typos` to confirm."
)


@dataclass(frozen=True)
class _BatchOutcome:
    """Merged result of running typos over one or more argv batches.

    Attributes:
        issues: Every typo parsed across all batches, in batch order.
        fatal_outputs: Display output of each batch that failed outright —
            an ``error`` diagnostic, or a non-zero exit with nothing
            parseable. Those are genuine tool failures (bad config, unreadable
            path, failed write) as opposed to typos' normal non-zero "I found
            something" exit, and they must not be swallowed just because a
            sibling batch did report typos.
        output: Combined display output of every batch.
        timed_out: Whether a batch exceeded the timeout. Batching stops there,
            but the findings collected from earlier batches are preserved.
    """

    issues: list[TyposIssue]
    fatal_outputs: list[str]
    output: str
    timed_out: bool = False

    @property
    def failed(self) -> bool:
        """Whether any batch failed outright.

        Returns:
            True when at least one batch failed or timed out.
        """
        return bool(self.fatal_outputs)

    def failure_message(self, default: str) -> str:
        """Compose the message describing the failed batches.

        Args:
            default: Message to use when the failed batches produced no output.

        Returns:
            The combined failure text, or ``default`` when there is none.
        """
        return "\n".join(t for t in self.fatal_outputs if t) or default


@register_tool
@dataclass
class TyposPlugin(BaseToolPlugin):
    """typos spell-checker plugin for Lintro.

    Integrates typos with Lintro to detect (and optionally auto-correct)
    misspellings in source code and documentation.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="typos",
            description=(
                "Source-code spell checker that finds and corrects typos with a "
                "low false-positive rate"
            ),
            can_fix=True,
            tool_type=ToolType.LINTER,
            file_patterns=TYPOS_FILE_PATTERNS,
            priority=TYPOS_DEFAULT_PRIORITY,
            conflicts_with=[],
            native_configs=list(TYPOS_CONFIG_FILENAMES),
            version_command=["typos", "--version"],
            min_version=get_min_version(ToolName.TYPOS),
            default_options={
                "timeout": TYPOS_DEFAULT_TIMEOUT,
            },
            default_timeout=TYPOS_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set typos-specific options.

        Args:
            **kwargs: Tool options (currently only shared options such as
                ``timeout`` are supported).
        """
        super().set_options(**kwargs)

    def _build_command(self, cwd: str | None = None) -> list[str]:
        """Build the base typos command.

        Args:
            cwd: Directory the command will run in, used to resolve the
                executable.

        Returns:
            List of command arguments (without file paths).
        """
        return self._get_executable_command(tool_name="typos", cwd=cwd) + [
            "--format",
            TYPOS_DEFAULT_FORMAT,
            # Lintro always passes an explicit file list, and typos (like
            # ripgrep) skips its ignore rules for paths named on the command
            # line unless this flag is set. Without it a project's
            # ``.typos.toml`` ``extend-exclude`` would be silently ignored.
            "--force-exclude",
        ]

    @staticmethod
    def _text_files(files: list[str], cwd: str | None) -> list[str]:
        """Drop binary files from an explicit file list.

        typos only auto-detects binary content for files it discovers itself;
        paths named on the command line are read as text regardless. Since
        lintro always passes an explicit list, filter here so ``fix`` can never
        rewrite bytes inside an image or other binary asset. Known binary
        suffixes are skipped first; remaining files are dropped when the first
        8 KiB contains a NUL.

        Args:
            files: Candidate paths, relative to ``cwd`` when it is set.
            cwd: Working directory the paths are relative to.

        Returns:
            The subset of ``files`` that look like text.
        """
        base = Path(cwd) if cwd else Path.cwd()
        text_files: list[str] = []
        for rel in files:
            suffix = Path(rel).suffix.lower()
            if suffix in BINARY_PATH_SUFFIXES:
                logger.debug(f"[TyposPlugin] Skipping binary suffix: {rel}")
                continue
            try:
                with (base / rel).open("rb") as handle:
                    chunk = handle.read(BINARY_SNIFF_BYTES)
            except OSError:
                # Unreadable paths are left for typos to report on.
                text_files.append(rel)
                continue
            if b"\x00" in chunk:
                logger.debug(f"[TyposPlugin] Skipping binary file: {rel}")
                continue
            text_files.append(rel)
        return text_files

    def _no_files_result(self, cwd: str | None) -> ToolResult:
        """Build the result used when every candidate file was binary.

        Args:
            cwd: Working directory the run was prepared for.

        Returns:
            A clean, successful ToolResult with nothing to report.
        """
        return ToolResult(
            name=self.definition.name,
            success=True,
            output=None,
            issues_count=0,
            issues=[],
            cwd=cwd,
        )

    def _run_batched(
        self,
        files: list[str],
        ctx: ExecutionContext,
        extra_args: list[str] | None = None,
        stop_on_failure: bool = False,
    ) -> _BatchOutcome:
        """Run typos over ARG_MAX-safe batches of ``files`` and merge results.

        typos has a catch-all file pattern, so a large tree would otherwise
        expand into one argv that exceeds the OS ``ARG_MAX`` limit and fails
        with ``E2BIG``.

        Batch failures never discard sibling results: a timeout stops the loop
        but keeps everything parsed so far, and a fatal batch is recorded
        alongside the findings other batches produced.

        Args:
            files: Paths to scan, relative to ``ctx.cwd``.
            ctx: Prepared execution context (cwd and timeout).
            extra_args: Extra flags appended to the base command, e.g.
                ``["--write-changes"]``.
            stop_on_failure: Stop after the first failing batch. Set for the
                mutating ``--write-changes`` pass so a fatal batch does not
                keep rewriting later ones.

        Returns:
            A :class:`_BatchOutcome` carrying the merged issues, the output of
            any batch that failed outright, and the combined display output.
        """
        base_cmd = self._build_command(cwd=ctx.cwd) + list(extra_args or [])
        batches = chunk_paths(files, fixed_arg_bytes=argv_cost(base_cmd))
        logger.debug(
            f"[TyposPlugin] Scanning {len(files)} files in {len(batches)} "
            f"batch(es) (cwd={ctx.cwd})",
        )

        issues: list[TyposIssue] = []
        outputs: list[str] = []
        fatal_outputs: list[str] = []
        timed_out = False
        for batch in batches:
            try:
                proc = self._run_subprocess_result(
                    cmd=base_cmd + batch,
                    timeout=ctx.timeout,
                    cwd=ctx.cwd,
                )
            except subprocess.TimeoutExpired:
                # Stop issuing batches — the run is already over budget — but
                # keep what earlier batches found rather than reporting zero.
                timed_out = True
                fatal_outputs.append(
                    f"typos timed out after {ctx.timeout}s on a batch of "
                    f"{len(batch)} file(s).",
                )
                break
            except OSError as exc:
                # E2BIG on a single over-budget path, or a vanished binary.
                # Convert to a tool failure the way TruffleHog does instead of
                # letting the executor report "Failed to initialize tool".
                fatal_outputs.append(f"typos failed to execute: {exc}")
                break
            # typos writes its JSON report to stdout and diagnostics to stderr;
            # parse stdout only so a stderr warning cannot corrupt the report.
            # parse_typos_report pairs findings with diagnostics so a
            # findings-only parse cannot treat an error stream as clean.
            report = parse_typos_report(output=proc.stdout)
            issues.extend(report.issues)
            if proc.output:
                outputs.append(proc.output)
            # typos exits 0 when clean and 2 when it reports typos. Failures
            # are tracked per batch so a sibling batch that did report typos
            # cannot hide them. Two signals matter, because a single batch can
            # both report a typo for one file and fail on another:
            #   1. explicit ``error`` records on stdout (unreadable file, ...);
            #   2. a non-zero exit with nothing parseable at all (bad config,
            #      a usage error typos only wrote to stderr).
            if report.diagnostics:
                fatal_outputs.extend(report.diagnostics)
            elif not proc.success:
                # Exit 2 is "I found typos". Any other non-zero is a runtime
                # failure (config/IO/usage) even when some JSON findings were
                # also emitted, so fix() must not continue to --write-changes.
                if proc.returncode != TYPOS_ISSUES_EXIT_CODE:
                    fatal_outputs.append(
                        proc.output or f"typos exited {proc.returncode}.",
                    )
                elif not report.issues:
                    fatal_outputs.append(proc.output or "")
            if fatal_outputs and stop_on_failure:
                break
        return _BatchOutcome(
            issues=issues,
            fatal_outputs=fatal_outputs,
            output="\n".join(outputs),
            timed_out=timed_out,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files for typos.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self._prepare_execution(paths=paths, options=options)
        if ctx.should_skip:
            # Framework invariant: should_skip is True only when the context
            # carries an early_result, so the Optional narrowing is safe.
            return ctx.early_result  # type: ignore[return-value]

        files = self._text_files(files=list(ctx.rel_files), cwd=ctx.cwd)
        if not files:
            return self._no_files_result(cwd=ctx.cwd)

        outcome = self._run_batched(files=files, ctx=ctx)
        issues = outcome.issues

        # A batch that failed outright (error record, or a non-zero exit with
        # nothing parseable) is a runtime problem. Report it even when another
        # batch produced findings, so the failure is never swallowed — and
        # keep those findings rather than reporting an empty run.
        if outcome.failed:
            return ToolResult(
                name=self.definition.name,
                success=False,
                timed_out=outcome.timed_out,
                output=outcome.failure_message("typos exited with an error."),
                issues_count=len(issues),
                issues=issues,
                cwd=ctx.cwd,
            )

        return ToolResult(
            name=self.definition.name,
            success=not issues,
            output=outcome.output if issues else None,
            issues_count=len(issues),
            issues=issues,
            cwd=ctx.cwd,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Auto-correct typos with ``typos --write-changes``.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results, satisfying the invariant
            ``initial = fixed + remaining``.
        """
        ctx = self._prepare_execution(
            paths=paths,
            options=options,
            no_files_message="No files to fix.",
        )
        if ctx.should_skip:
            # Framework invariant: should_skip is True only when the context
            # carries an early_result, so the Optional narrowing is safe.
            return ctx.early_result  # type: ignore[return-value]

        files = self._text_files(files=list(ctx.rel_files), cwd=ctx.cwd)
        if not files:
            return self._no_files_result(cwd=ctx.cwd)

        # Detect issues before fixing.
        initial = self._run_batched(files=files, ctx=ctx)
        initial_issues = initial.issues
        initial_count = len(initial_issues)

        # Mirror check(): a batch that failed outright means typos never ran
        # properly there (bad config, unreadable path, timeout). Stop before
        # the mutating --write-changes pass rather than writing on the
        # strength of a partially failed detection — but keep the findings the
        # successful batches did produce.
        if initial.failed:
            return self._error_result(
                message=initial.failure_message("typos exited with an error."),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
                timed_out=initial.timed_out,
            )

        # Apply corrections in place. A clean ``--write-changes`` run prints
        # nothing and exits 0 (verified against typos 1.49.0), so any error
        # record or non-zero exit without a parseable report is a real write
        # failure. ``stop_on_failure`` keeps a failing batch from rewriting
        # the batches that follow it.
        written = self._run_batched(
            files=files,
            ctx=ctx,
            extra_args=["--write-changes"],
            stop_on_failure=True,
        )
        if written.failed:
            return self._error_result(
                message=written.failure_message(
                    "typos --write-changes exited with an error.",
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
                # --write-changes already ran, so files may have been
                # corrected on disk even though the pass did not complete.
                after_write=True,
                timed_out=written.timed_out,
            )

        # Re-check for anything typos could not auto-correct.
        recheck = self._run_batched(files=files, ctx=ctx)
        if recheck.failed:
            return self._error_result(
                message=recheck.failure_message(
                    "typos re-check exited with an error.",
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
                after_write=True,
                timed_out=recheck.timed_out,
            )

        remaining_issues = recheck.issues
        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)
        # A re-check can report more issues than the pre-scan (new findings
        # after a rewrite). Grow initial so ToolResult's
        # initial = fixed + remaining invariant cannot crash format.
        if initial_count != fixed_count + remaining_count:
            initial_count = fixed_count + remaining_count

        return ToolResult(
            name=self.definition.name,
            success=remaining_count == 0,
            output=_fix_summary(fixed=fixed_count, remaining=remaining_count),
            # Only the surviving typos are reported as issues: unfixed typos
            # appear in both the initial and post-fix reports, so combining
            # the lists would double-count them.
            issues_count=remaining_count,
            issues=remaining_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=fixed_count,
            remaining_issues_count=remaining_count,
            initial_issues=initial_issues or None,
            cwd=ctx.cwd,
        )

    def _error_result(
        self,
        message: str,
        initial_issues: list[TyposIssue],
        cwd: str | None,
        after_write: bool = False,
        timed_out: bool = False,
    ) -> ToolResult:
        """Build the ToolResult for a failed fix pass.

        Args:
            message: Human-readable failure message.
            initial_issues: Issues detected before the failing pass.
            cwd: Working directory the command ran in.
            after_write: True when ``--write-changes`` already ran, so files
                may have been corrected even though the counts say nothing was.
            timed_out: True when the failure was a timeout.

        Returns:
            ToolResult reporting the failure with nothing counted as fixed.
        """
        initial_count = len(initial_issues)
        if after_write:
            message = f"{message}\n{_AFTER_WRITE_NOTE}"
        return ToolResult(
            name=self.definition.name,
            success=False,
            timed_out=timed_out,
            output=message,
            issues_count=initial_count,
            issues=initial_issues,
            initial_issues_count=initial_count,
            fixed_issues_count=0,
            remaining_issues_count=initial_count,
            initial_issues=initial_issues or None,
            cwd=cwd,
        )


def _fix_summary(fixed: int, remaining: int) -> str:
    """Compose the human-readable summary of a fix run.

    Args:
        fixed: Number of typos typos corrected.
        remaining: Number of typos that survived the fix pass.

    Returns:
        A one-line summary of the fix outcome.
    """
    if fixed and remaining:
        return f"Fixed {fixed} typo(s); {remaining} could not be auto-corrected."
    if fixed:
        return f"Fixed {fixed} typo(s)."
    if remaining:
        return f"Found {remaining} typo(s) that could not be fixed."
    return "No typos found."
