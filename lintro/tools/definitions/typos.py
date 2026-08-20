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
from lintro.parsers.typos.typos_parser import parse_typos_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.timeout_utils import create_timeout_result

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
# Bytes sampled from the head of each file when sniffing for binary content.
BINARY_SNIFF_BYTES: int = 8192


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
        rewrite bytes inside an image or other binary asset.

        Args:
            files: Candidate paths, relative to ``cwd`` when it is set.
            cwd: Working directory the paths are relative to.

        Returns:
            The subset of ``files`` that look like text.
        """
        base = Path(cwd) if cwd else Path.cwd()
        text_files: list[str] = []
        for rel in files:
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

    def _timeout_result(
        self,
        cmd: list[str],
        timeout: int,
        cwd: str | None,
        initial_issues: list[TyposIssue] | None = None,
        after_write: bool = False,
    ) -> ToolResult:
        """Build the ToolResult returned when a typos run times out.

        Args:
            cmd: Command that timed out.
            timeout: Timeout in seconds that was exceeded.
            cwd: Working directory the command ran in.
            initial_issues: Issues detected before the timeout, when the
                timeout happened during a fix run.
            after_write: True when ``--write-changes`` already completed, so
                the counts below understate what was actually corrected.

        Returns:
            ToolResult describing the timeout.
        """
        base = create_timeout_result(tool=self, timeout=timeout, cmd=cmd)
        issues = initial_issues or []
        initial_count = len(issues)
        output = base.output
        if after_write:
            # ``--write-changes`` already ran, so files on disk may be fully or
            # partially corrected even though the verification pass never
            # completed. Say so rather than implying nothing was fixed.
            output = (
                f"{output}\n"
                "Note: typos --write-changes already ran, so files may have "
                "been corrected on disk. The fixed/remaining counts below "
                "could not be verified and are reported conservatively; "
                "re-run `lintro check --tools typos` to confirm."
            )
        return ToolResult(
            name=self.definition.name,
            success=base.success,
            timed_out=base.timed_out,
            output=output,
            issues_count=initial_count,
            issues=issues,
            initial_issues_count=initial_count,
            fixed_issues_count=0,
            remaining_issues_count=initial_count,
            initial_issues=issues or None,
            cwd=cwd,
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

        cmd = self._build_command(cwd=ctx.cwd) + files
        logger.debug(f"[TyposPlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})")
        try:
            proc = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._timeout_result(cmd=cmd, timeout=ctx.timeout, cwd=ctx.cwd)

        # typos writes its JSON report to stdout and diagnostics to stderr;
        # parse stdout only so a stderr warning cannot corrupt the report.
        issues = parse_typos_output(output=proc.stdout)

        # typos exits 0 when clean and 2 when it reports typos. Any other
        # non-zero exit with nothing parseable is a runtime problem (bad
        # config, unreadable path) and must not read as a clean pass.
        if not issues and not proc.success:
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(proc.output or "typos exited with an error."),
                issues_count=0,
                parse_failures_count=1,
                cwd=ctx.cwd,
            )

        return ToolResult(
            name=self.definition.name,
            success=not issues,
            output=proc.output if issues else None,
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
        check_cmd = self._build_command(cwd=ctx.cwd) + files
        try:
            initial_proc = self._run_subprocess_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._timeout_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )

        initial_issues = parse_typos_output(output=initial_proc.stdout)
        initial_count = len(initial_issues)

        # Apply corrections in place. typos exits non-zero when it reports
        # typos (including ones it just fixed), so only a failure with no
        # parseable report signals a real write/tool error. The JSON format is
        # kept for the fix pass so that guard can tell the two apart.
        fix_cmd = [
            *self._build_command(cwd=ctx.cwd),
            "--write-changes",
            *files,
        ]
        try:
            fix_proc = self._run_subprocess_result(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._timeout_result(
                cmd=fix_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                initial_issues=initial_issues,
            )

        if not fix_proc.success and not parse_typos_output(output=fix_proc.stdout):
            return self._error_result(
                message=(
                    fix_proc.output or "typos --write-changes exited with an error."
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
            )

        # Re-check for anything typos could not auto-correct.
        try:
            remaining_proc = self._run_subprocess_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._timeout_result(
                cmd=check_cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                initial_issues=initial_issues,
                after_write=True,
            )

        remaining_issues = parse_typos_output(output=remaining_proc.stdout)
        if not remaining_issues and not remaining_proc.success:
            return self._error_result(
                message=(
                    remaining_proc.output or "typos re-check exited with an error."
                ),
                initial_issues=initial_issues,
                cwd=ctx.cwd,
            )

        remaining_count = len(remaining_issues)
        fixed_count = max(0, initial_count - remaining_count)

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
    ) -> ToolResult:
        """Build the ToolResult for a failed fix pass.

        Args:
            message: Human-readable failure message.
            initial_issues: Issues detected before the failing pass.
            cwd: Working directory the command ran in.

        Returns:
            ToolResult reporting the failure with nothing counted as fixed.
        """
        initial_count = len(initial_issues)
        return ToolResult(
            name=self.definition.name,
            success=False,
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
