"""Cargo-audit tool definition.

Cargo-audit is a security tool for Rust projects that scans Cargo.lock
for dependencies with known security vulnerabilities from the RustSec
advisory database.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_outcome import Checked, Skipped, TimedOut, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.cargo_audit.cargo_audit_issue import CargoAuditIssue
from lintro.parsers.cargo_audit.cargo_audit_parser import (
    extract_cargo_audit_payload,
    parse_cargo_audit_output,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.option_validators import (
    filter_none_options,
    validate_positive_int,
)
from lintro.utils.path_filtering import (
    resolve_exclude_anchors,
    should_exclude_path,
    walk_files_with_excludes,
)

# Constants for cargo-audit configuration
CARGO_AUDIT_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
CARGO_AUDIT_LOCKFILE: str = "Cargo.lock"
CARGO_AUDIT_FILE_PATTERNS: list[str] = [CARGO_AUDIT_LOCKFILE]


def _lockfiles(files: list[str]) -> list[Path]:
    """Return the distinct lockfiles among discovered files.

    Every ``Cargo.lock`` is a separate dependency resolution, so each one is
    audited. There is no built-in exclusion for ``vendor/`` or ``target/``:
    scope is decided by lintro's shared exclude patterns, the same way for
    every tool and every caller (#3549).

    Args:
        files: Files handed over by lintro's file discovery.

    Returns:
        Resolved lockfile paths, de-duplicated and sorted for a deterministic
        run order.
    """
    return sorted(
        {Path(f).resolve() for f in files if Path(f).name == CARGO_AUDIT_LOCKFILE},
    )


def _shown(lockfile: Path) -> str:
    """Name a lockfile in output, relative to the process directory if below it.

    Args:
        lockfile: Resolved lockfile path.

    Returns:
        The relative path when the lockfile lies below the working directory,
        otherwise the absolute path.
    """
    try:
        return str(lockfile.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(lockfile)


def _names(lockfiles: list[Path]) -> str:
    """Join lockfile names for a one-line note.

    Args:
        lockfiles: Lockfiles to name.

    Returns:
        Comma-separated lockfile names.
    """
    return ", ".join(_shown(lockfile) for lockfile in lockfiles)


@dataclass(frozen=True)
class _LockfileAudit:
    """The interpreted result of auditing one Cargo.lock.

    Attributes:
        lockfile: The audited lockfile, resolved.
        issues: Vulnerabilities parsed from its report, each naming it.
        complete: Whether cargo-audit produced a usable report that explains
            its exit status.
        parse_failed: Whether the report was missing or schema-invalid.
        output: Combined command output, kept for display.
    """

    lockfile: Path
    issues: list[CargoAuditIssue]
    complete: bool
    parse_failed: bool
    output: str


@dataclass(frozen=True)
class _AuditSummary:
    """Every audit of one check, plus the lockfiles left unaudited.

    Attributes:
        audits: Audits that ran to completion of the subprocess, in run order.
        not_audited: Lockfiles the timeout budget did not reach.
        excluded: Lockfiles removed by lintro's exclude patterns.
        timeout: The whole check's timeout budget in seconds.
    """

    audits: list[_LockfileAudit]
    not_audited: list[Path]
    excluded: list[Path]
    timeout: float

    @property
    def issues(self) -> list[CargoAuditIssue]:
        """Return every finding, in run order.

        Returns:
            Findings from all audits.
        """
        return [issue for audit in self.audits for issue in audit.issues]

    @property
    def incomplete(self) -> list[Path]:
        """Return the audited lockfiles without a complete report.

        Returns:
            Lockfiles whose audit failed or could not be parsed.
        """
        return [audit.lockfile for audit in self.audits if not audit.complete]

    def incomplete_note(self) -> str | None:
        """Name the lockfiles whose audit did not complete.

        Returns:
            A one-line note, or None when every audit completed.
        """
        if not self.incomplete:
            return None
        return (
            "cargo-audit did not produce a complete report for "
            f"{_names(self.incomplete)}; those lockfiles are not audited."
        )

    def text(self) -> str | None:
        """Render the output a result shows.

        Returns:
            The report of every audit with findings or without a complete
            report, each headed by its lockfile, followed by notes naming
            the lockfiles that were not audited and why. None when there is
            nothing to show.
        """
        blocks = [
            f"cargo-audit report for {_shown(audit.lockfile)}:\n{audit.output}"
            for audit in self.audits
            if audit.issues or not audit.complete
        ]
        notes = [
            self.incomplete_note(),
            (
                f"cargo-audit timed out after {self.timeout:g}s; not audited: "
                f"{_names(self.not_audited)}."
                if self.not_audited
                else None
            ),
            (
                "Not audited, excluded by lintro's exclude patterns: "
                f"{_names(self.excluded)}."
                if self.excluded
                else None
            ),
        ]
        shown = blocks + [note for note in notes if note]
        return "\n\n".join(shown) if shown else None


@register_tool
@dataclass
class CargoAuditPlugin(BaseToolPlugin):
    """Cargo-audit plugin for Lintro.

    Provides security vulnerability scanning for Rust dependencies using
    cargo-audit to check against the RustSec advisory database.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with cargo-audit configuration.
        """
        return ToolDefinition(
            name="cargo_audit",
            description="Security vulnerability scanner for Rust dependencies",
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=CARGO_AUDIT_FILE_PATTERNS,
            claims=[
                Claim(
                    patterns=CARGO_AUDIT_FILE_PATTERNS,
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=False,
            native_configs=[".cargo/audit.toml"],
            version_command=["cargo", "audit", "--version"],
            min_version=get_min_version(ToolName.CARGO_AUDIT),
            default_options={
                "timeout": CARGO_AUDIT_DEFAULT_TIMEOUT,
            },
            default_timeout=CARGO_AUDIT_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Set tool-specific options.

        Args:
            timeout: Timeout in seconds, one budget shared by every lockfile
                audited in a check.
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def _build_command(self, lockfile: Path, *, fetch: bool = True) -> list[str]:
        """Build the cargo-audit command for one lockfile.

        Args:
            lockfile: Absolute path of the lockfile to audit. It is passed as
                ``--file=<path>``, one token, so a path starting with ``-``
                can be read neither as an option nor as cargo-audit's ``-``
                (standard input).
            fetch: Whether cargo-audit may update the advisory database. Once
                one lockfile has produced a report the database is in place,
                so later lockfiles reuse it with ``--no-fetch``.

        Returns:
            Command list for running cargo-audit with JSON output.
        """
        cmd = ["cargo", "audit", "--json", f"--file={lockfile}"]
        if not fetch:
            cmd.append("--no-fetch")
        return cmd

    def _audit_lockfile(
        self,
        lockfile: Path,
        *,
        timeout: float,
        fetch: bool,
    ) -> _LockfileAudit:
        """Run cargo-audit on one lockfile and interpret its report.

        Args:
            lockfile: Absolute path of the lockfile.
            timeout: Seconds left in the check's budget.
            fetch: Whether cargo-audit may update the advisory database.

        Returns:
            The interpreted audit. Each finding's ``file`` is the lockfile,
            so findings from different lockfiles stay apart.
        """
        # Run from the lockfile's directory so cargo-audit finds that
        # project's ``.cargo/audit.toml``.
        proc = self._run_subprocess_result(
            self._build_command(lockfile, fetch=fetch),
            timeout=timeout,
            cwd=str(lockfile.parent),
        )
        # cargo-audit writes its JSON report to stdout; parse stdout only so a
        # stderr warning cannot corrupt parsing (see #1043).
        payload = extract_cargo_audit_payload(proc.stdout)
        issues = parse_cargo_audit_output(proc.stdout, data=payload)
        for issue in issues:
            issue.file = str(lockfile)
        # A missing or schema-invalid report cannot establish a clean scan,
        # even when the subprocess succeeds (see #1044 and #3003). A non-zero
        # exit is explained only by findings; without them it is a failure.
        parse_failed = payload is None
        return _LockfileAudit(
            lockfile=lockfile,
            issues=issues,
            complete=not parse_failed and (proc.success or bool(issues)),
            parse_failed=parse_failed,
            output=proc.output,
        )

    def _excluded_lockfiles(self, paths: list[str]) -> list[Path]:
        """Return the lockfiles below ``paths`` that the exclude patterns drop.

        Applies the same patterns and anchors as lintro's file discovery, so
        it names exactly the lockfiles discovery left out.

        Args:
            paths: Paths passed to ``check``.

        Returns:
            Resolved, sorted lockfiles removed by an exclude pattern.
        """
        if not self.exclude_patterns:
            return []
        anchors = resolve_exclude_anchors(paths)
        candidates = walk_files_with_excludes(
            paths=paths,
            file_patterns=CARGO_AUDIT_FILE_PATTERNS,
            exclude_patterns=[],
            include_venv=self.include_venv,
        )
        return sorted(
            {
                Path(candidate).resolve()
                for candidate in candidates
                if should_exclude_path(candidate, self.exclude_patterns, anchors)
            },
        )

    def _audit_all(
        self,
        lockfiles: list[Path],
        *,
        timeout: float,
        excluded: list[Path],
    ) -> _AuditSummary:
        """Audit each lockfile within one timeout budget for the whole check.

        Args:
            lockfiles: Lockfiles to audit, in run order.
            timeout: The check's timeout budget in seconds.
            excluded: Lockfiles removed by lintro's exclude patterns.

        Returns:
            Every audit that finished, and the lockfiles the budget did not
            reach (including the one whose audit timed out).
        """
        deadline = time.monotonic() + timeout
        audits: list[_LockfileAudit] = []
        for lockfile in lockfiles:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # A parsed report proves the advisory database is in place.
            fetch = all(audit.parse_failed for audit in audits)
            try:
                audits.append(
                    self._audit_lockfile(lockfile, timeout=remaining, fetch=fetch),
                )
            except subprocess.TimeoutExpired:
                break
        return _AuditSummary(
            audits=audits,
            not_audited=lockfiles[len(audits) :],
            excluded=excluded,
            timeout=timeout,
        )

    def doc_url(self, code: str) -> str | None:
        """Return RustSec advisory URL for the given advisory ID.

        Args:
            code: RUSTSEC advisory ID (e.g., "RUSTSEC-2021-0124").

        Returns:
            URL to the RustSec advisory page, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.CARGO_AUDIT.format(code=code)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Rust dependencies for security vulnerabilities.

        Args:
            paths: List of paths to check.
            options: Additional options for the check.

        Returns:
            ToolResult with security scan results.
        """
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            excluded = self._excluded_lockfiles(paths) if ctx.no_files else []
            if not excluded:
                return ctx
            # Exclusion is the user's choice, so this is a skip, but a
            # security scanner names what it did not read rather than claim
            # it found nothing (#3549).
            return ToolResult.from_outcome(
                ToolOutcome(
                    execution=Skipped(
                        "every Cargo.lock found is excluded by lintro's "
                        f"exclude patterns, not audited: {_names(excluded)}",
                    ),
                ),
                name=self.definition.name,
            )

        summary = self._audit_all(
            _lockfiles(ctx.files),
            timeout=ctx.timeout,
            excluded=self._excluded_lockfiles(paths),
        )
        issues = summary.issues
        incomplete_note = summary.incomplete_note()
        payload: dict[str, Any] = {
            "name": self.definition.name,
            "issues": issues,
            "parse_failures_count": sum(audit.parse_failed for audit in summary.audits),
            # A lockfile without a complete report was not audited, whatever
            # the other lockfiles found.
            "execution_failed": incomplete_note is not None,
        }
        if summary.not_audited:
            return ToolResult.from_outcome(
                ToolOutcome(
                    execution=TimedOut(summary.text()),
                    issues_count=len(issues),
                ),
                failure_besides_timeout=incomplete_note,
                **payload,
            )
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(
                    success=not issues and incomplete_note is None,
                    inspected_files=True,
                ),
                issues_count=len(issues),
            ),
            output=summary.text(),
            **payload,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Cargo-audit cannot automatically fix vulnerabilities.

        Args:
            paths: List of paths (unused).
            options: Additional options (unused).

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Always, as cargo-audit cannot auto-fix.
        """
        raise NotImplementedError(
            "cargo-audit cannot automatically fix vulnerabilities. "
            "Update dependencies manually using `cargo update`.",
        )
