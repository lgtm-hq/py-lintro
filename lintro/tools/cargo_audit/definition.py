"""Cargo-audit tool definition.

Cargo-audit is a security tool for Rust projects that scans Cargo.lock
for dependencies with known security vulnerabilities from the RustSec
advisory database.
"""

from __future__ import annotations

import os
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
from lintro.models.core.tool_outcome import Checked, NoFiles, TimedOut, ToolOutcome
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
from lintro.utils.git_diff import filter_files_by_diff_for_paths
from lintro.utils.path_filtering import (
    resolve_exclude_anchors,
    should_exclude_path,
)
from lintro.utils.tool_utils import VENV_PATTERNS

# Constants for cargo-audit configuration
CARGO_AUDIT_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
CARGO_AUDIT_LOCKFILE: str = "Cargo.lock"
CARGO_AUDIT_FILE_PATTERNS: list[str] = [CARGO_AUDIT_LOCKFILE]


# Lists of lockfiles in notes name this many, then give a count of the rest.
_NAMED_LIMIT: int = 10


def _lockfiles(files: list[str]) -> list[Path]:
    """Return the distinct lockfiles among discovered files.

    Every ``Cargo.lock`` is a separate dependency resolution, so each one is
    audited. There is no built-in exclusion for ``vendor/`` or ``target/``:
    scope is decided by lintro's shared exclude patterns, the same way for
    every tool and every caller (#3549).

    Args:
        files: Files handed over by lintro's file discovery.

    Returns:
        Absolute lockfile paths, as the user sees them (symlinks are not
        resolved), de-duplicated and sorted for a deterministic run order.
    """
    return sorted(
        {
            Path(os.path.abspath(f))
            for f in files
            if Path(f).name == CARGO_AUDIT_LOCKFILE
        },
    )


def _shown(lockfile: Path | str) -> str:
    """Name a lockfile in output, relative to the process directory if below it.

    Args:
        lockfile: Absolute lockfile path.

    Returns:
        The relative path when the lockfile lies below the working directory,
        otherwise the absolute path.
    """
    try:
        return str(Path(lockfile).relative_to(Path.cwd()))
    except ValueError:
        return str(lockfile)


def _names(labels: list[str]) -> str:
    """Join names for a one-line note, naming at most ``_NAMED_LIMIT``.

    Args:
        labels: Lockfile names, already shown relative where possible.

    Returns:
        The count, then comma-separated names and how many more there are.
    """
    shown = ", ".join(labels[:_NAMED_LIMIT])
    rest = len(labels) - _NAMED_LIMIT
    return f"({len(labels)}): {shown}" + (f" and {rest} more" if rest > 0 else "")


def _lockfile_names(lockfiles: list[Path]) -> str:
    """Join lockfile paths for a one-line note.

    Args:
        lockfiles: Lockfiles to name.

    Returns:
        The count and the first names, see ``_names``.
    """
    return _names([_shown(lockfile) for lockfile in lockfiles])


def _denied_warnings(payload: dict[str, Any] | None) -> list[str]:
    """Name the warnings cargo-audit denied (``-D`` or ``[output] deny``).

    Args:
        payload: The parsed report, or None when there was none.

    Returns:
        One label per warning marked ``"denied": true``, such as
        ``unmaintained ansi_term (RUSTSEC-2021-0139)``.
    """
    warnings = payload.get("warnings") if payload else None
    if not isinstance(warnings, dict):
        return []
    labels: list[str] = []
    for kind, entries in warnings.items():
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("denied") is not True:
                continue
            package, advisory = entry.get("package"), entry.get("advisory")
            name = package.get("name") if isinstance(package, dict) else None
            ident = advisory.get("id") if isinstance(advisory, dict) else None
            labels.append(f"{kind} {name or '?'}" + (f" ({ident})" if ident else ""))
    return labels


def _report_problem(
    payload: dict[str, Any] | None,
    issues: list[CargoAuditIssue],
    returncode: int,
    *,
    denied: bool,
) -> str | None:
    """Say why a cargo-audit report does not establish a complete audit.

    cargo-audit 0.23 exits 0 for a clean audit, 1 when it found
    vulnerabilities or denied a warning, and 2 on an error (an unreadable
    lockfile, a bad argument). Only a valid report with the matching exit
    status is complete.

    Args:
        payload: The parsed report, or None when there was none.
        issues: Findings parsed from it.
        returncode: cargo-audit's exit status.
        denied: Whether the report marks a warning as denied.

    Returns:
        The reason the audit is incomplete, or None when it is complete.
    """
    if payload is None:
        return "no usable JSON report"
    vulnerabilities = payload["vulnerabilities"]
    records = len(vulnerabilities["list"])
    if len(issues) != records:
        return f"{records - len(issues)} of {records} vulnerability records unreadable"
    count, found = vulnerabilities.get("count"), vulnerabilities.get("found")
    if type(count) is not int or count != records or found is not (records > 0):
        return f"report says found={found}, count={count} but lists {records}"
    # Every finding is displayed and fingerprinted by advisory, package and
    # version; a record without them cannot be told apart from another.
    missing = sorted(
        {
            field
            for issue in issues
            for field, value in (
                ("package name", issue.package_name),
                ("package version", issue.package_version),
            )
            if not value
        },
    )
    if missing:
        partial = sum(not (i.package_name and i.package_version) for i in issues)
        return (
            f"{partial} of {records} vulnerability records without "
            f"{' or '.join(missing)}"
        )
    lockfile = payload.get("lockfile")
    if isinstance(lockfile, dict) and lockfile.get("dependency-count") == 0:
        # Cargo always records at least the root package, even for a crate
        # with no dependencies, so a lockfile with none is not a real one.
        return "lockfile lists no packages"
    expected = 1 if issues or denied else 0
    if returncode != expected:
        return f"exit status {returncode}, expected {expected}"
    return None


@dataclass(frozen=True)
class _LockfileAudit:
    """The interpreted result of auditing one Cargo.lock.

    Attributes:
        lockfile: The audited lockfile, as the user sees it.
        issues: Vulnerabilities parsed from its report, each naming it.
        problem: Why the report does not establish a complete audit, or None.
        parsed: Whether cargo-audit printed a report, which proves the
            advisory database is in place.
        parse_failures: 1 for a missing report, else its unreadable records.
        denied: Warnings cargo-audit denied, which fail a complete audit.
        output: Combined command output, kept for display.
    """

    lockfile: Path
    issues: list[CargoAuditIssue]
    problem: str | None
    parsed: bool
    parse_failures: int
    denied: list[str]
    output: str

    @property
    def complete(self) -> bool:
        """Return whether cargo-audit produced a complete, valid report.

        Returns:
            True when nothing is wrong with the report.
        """
        return self.problem is None


@dataclass(frozen=True)
class _AuditSummary:
    """Every audit of one check, plus the lockfiles left unaudited.

    Attributes:
        audits: Audits that ran to completion of the subprocess, in run order.
        not_audited: Lockfiles the timeout budget did not reach.
        excluded: Lockfiles removed by lintro's exclude patterns.
        unreadable: Entries named ``Cargo.lock`` that are not readable files.
        outside: Audited symlinks whose target lies outside the project,
            with that target.
        timeout: The whole check's timeout budget in seconds.
    """

    audits: list[_LockfileAudit]
    not_audited: list[Path]
    excluded: list[Path]
    unreadable: list[Path]
    outside: list[tuple[Path, Path]]
    timeout: float

    @property
    def issues(self) -> list[CargoAuditIssue]:
        """Return every finding, in run order.

        Returns:
            Findings from all audits.
        """
        return [issue for audit in self.audits for issue in audit.issues]

    def failure_note(self) -> str | None:
        """Name the lockfiles that failed to be audited, and why.

        Returns:
            One line per kind of failure, or None when there is none.
        """
        failed = [
            f"{_shown(audit.lockfile)} ({audit.problem})"
            for audit in self.audits
            if not audit.complete
        ]
        notes = [
            (
                "cargo-audit did not produce a complete report, not audited "
                f"{_names(failed)}."
                if failed
                else None
            ),
            (
                "Not audited, named Cargo.lock but not a readable file "
                f"{_lockfile_names(self.unreadable)}."
                if self.unreadable
                else None
            ),
        ]
        shown = [note for note in notes if note]
        return "\n".join(shown) if shown else None

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
            if audit.issues or audit.denied or not audit.complete
        ]
        denied = [
            f"{_shown(audit.lockfile)} ({', '.join(audit.denied)})"
            for audit in self.audits
            if audit.denied
        ]
        notes = [
            self.failure_note(),
            (
                f"cargo-audit denied warnings, audited but failing {_names(denied)}."
                if denied
                else None
            ),
            (
                f"cargo-audit timed out after {self.timeout:g}s; not audited "
                f"{_lockfile_names(self.not_audited)}."
                if self.not_audited
                else None
            ),
            (
                "Not audited, excluded by lintro's exclude patterns "
                f"{_lockfile_names(self.excluded)}."
                if self.excluded
                else None
            ),
            *(
                f"{_shown(link)} is a symlink to {target}, outside the project; "
                f"audited and reported as {_shown(link)}."
                for link, target in self.outside
            ),
        ]
        shown = blocks + [note for note in notes if note]
        return "\n\n".join(shown) if shown else None


@dataclass(frozen=True)
class _Scan:
    """Every entry named Cargo.lock below the requested paths.

    Attributes:
        excluded: Entries an exclude pattern removes.
        unreadable: Other entries that are not readable regular files after
            resolving symlinks: directories, dangling links, link loops.
        readable: The other entries, readable lockfiles.
    """

    excluded: list[Path]
    unreadable: list[Path]
    readable: list[Path]


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
        # A missing, schema-invalid or inconsistent report, or an exit status
        # other than the one the report explains, cannot establish a clean
        # scan (see #1044 and #3003). Its valid findings are still kept.
        denied = _denied_warnings(payload)
        problem = _report_problem(
            payload,
            issues,
            proc.returncode,
            denied=bool(denied),
        )
        records = len(payload["vulnerabilities"]["list"]) if payload else 0
        return _LockfileAudit(
            lockfile=lockfile,
            issues=issues,
            problem=problem,
            parsed=payload is not None,
            parse_failures=1 if payload is None else records - len(issues),
            denied=denied,
            output=proc.output,
        )

    def _scan(self, paths: list[str]) -> _Scan:
        """Find every entry named Cargo.lock below ``paths``.

        Shared discovery silently drops entries it cannot read (directories,
        dangling links, link loops). A security scanner must name those, so
        this walk keeps them. Excludes use discovery's patterns and anchors.

        Args:
            paths: Paths passed to ``check``.

        Returns:
            The entries, sorted, split into excluded, unreadable and readable.
        """
        candidates: set[str] = set()
        for path in paths:
            # A requested directory named Cargo.lock is an entry too, not
            # only its children (``abspath`` drops a trailing slash).
            if os.path.basename(os.path.abspath(path)) == CARGO_AUDIT_LOCKFILE:
                candidates.add(os.path.abspath(path))
            if not os.path.isdir(path):
                continue
            for root, dirs, files in os.walk(path):
                if not self.include_venv:
                    dirs[:] = [d for d in dirs if d not in VENV_PATTERNS]
                candidates.update(
                    os.path.abspath(os.path.join(root, name))
                    for name in (*dirs, *files)
                    if name == CARGO_AUDIT_LOCKFILE
                )
        anchors = resolve_exclude_anchors(paths)
        excluded: list[Path] = []
        unreadable: list[Path] = []
        readable: list[Path] = []
        for candidate in sorted(candidates):
            if should_exclude_path(candidate, self.exclude_patterns, anchors):
                excluded.append(Path(candidate))
            elif os.path.isfile(candidate) and os.access(candidate, os.R_OK):
                readable.append(Path(candidate))
            else:
                unreadable.append(Path(candidate))
        return _Scan(excluded=excluded, unreadable=unreadable, readable=readable)

    def _in_scope(
        self,
        scan: _Scan,
        paths: list[str],
        options: dict[str, object],
    ) -> _Scan:
        """Keep the entries a ``--diff`` run covers, of every kind.

        Args:
            scan: Every Cargo.lock entry below ``paths``.
            paths: Paths passed to ``check``.
            options: Options passed to ``check``.

        Returns:
            ``scan`` unchanged without a diff base, else only changed entries.
        """
        base = options.get("diff_base", self.options.get("diff_base"))
        if not isinstance(base, str):
            return scan
        changed = set(
            filter_files_by_diff_for_paths(
                [str(e) for e in (*scan.excluded, *scan.unreadable, *scan.readable)],
                base,
                paths,
            ),
        )
        return _Scan(
            excluded=[e for e in scan.excluded if str(e) in changed],
            unreadable=[e for e in scan.unreadable if str(e) in changed],
            readable=[e for e in scan.readable if str(e) in changed],
        )

    def _no_files_result(self, ctx: ToolResult, scan: _Scan) -> ToolResult:
        """Explain a check in which discovery handed over no lockfile.

        Args:
            ctx: The no-files result from ``prepare``.
            scan: Every Cargo.lock entry in the requested scope.

        Returns:
            A no-files result naming the excluded lockfiles when the requested
            scope held lockfiles and an exclude pattern removed every one, else
            ``ctx``.
        """
        excluded, readable = scan.excluded, scan.readable
        # A readable lockfile in scope that was not excluded means the diff or
        # incremental filter left it out, not an exclusion (#3572). Excluded
        # lockfiles are not in the incremental cache, so an incremental run
        # without a diff base compares against the whole tree.
        if readable or not excluded:
            return ctx
        # Nothing is in scope by the user's own configuration, which is what
        # "no files" means for every tool (and what the no-silent-skip gate
        # accepts), but a security scanner names what it did not read (#3549).
        return ToolResult.from_outcome(
            ToolOutcome(execution=NoFiles()),
            name=self.definition.name,
            output=(
                "No Cargo.lock in scope: every one found is excluded by lintro's "
                f"exclude patterns, not audited {_names(list(map(_shown, excluded)))}."
            ),
        )

    def _audit_all(
        self,
        lockfiles: list[Path],
        *,
        timeout: float,
        scan: _Scan,
        anchors: tuple[str, ...],
    ) -> _AuditSummary:
        """Audit each lockfile within one timeout budget for the whole check.

        Args:
            lockfiles: Lockfiles to audit, in run order.
            timeout: The check's timeout budget in seconds.
            scan: Every Cargo.lock entry below the requested paths.
            anchors: Project directories; a symlinked lockfile whose target
                lies outside all of them is named in the output.

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
            fetch = not any(audit.parsed for audit in audits)
            try:
                audits.append(
                    self._audit_lockfile(lockfile, timeout=remaining, fetch=fetch),
                )
            except subprocess.TimeoutExpired:
                break
        return _AuditSummary(
            audits=audits,
            not_audited=lockfiles[len(audits) :],
            excluded=scan.excluded,
            unreadable=scan.unreadable,
            outside=[
                (lockfile, Path(os.path.realpath(lockfile)))
                for lockfile in lockfiles
                if not any(
                    Path(os.path.realpath(lockfile)).is_relative_to(
                        os.path.realpath(anchor),
                    )
                    for anchor in anchors
                )
            ],
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
        if isinstance(ctx, ToolResult) and not ctx.no_files:
            return ctx
        # In a --diff run every kind of entry is limited to the changed ones,
        # so unchanged entries neither fail the run nor fill its notes.
        scan = self._in_scope(self._scan(paths), paths, options)
        if isinstance(ctx, ToolResult) and not scan.unreadable:
            return self._no_files_result(ctx, scan)
        # Unreadable entries fail the check even when nothing else is in
        # scope, and each is reported once, as unreadable, not as a failed run.
        files = [] if isinstance(ctx, ToolResult) else ctx.files
        summary = self._audit_all(
            [f for f in _lockfiles(files) if f not in set(scan.unreadable)],
            timeout=0 if isinstance(ctx, ToolResult) else ctx.timeout,
            scan=scan,
            anchors=resolve_exclude_anchors(paths),
        )
        issues = summary.issues
        failure = summary.failure_note()
        denied = any(audit.denied for audit in summary.audits)
        payload: dict[str, Any] = {
            "name": self.definition.name,
            "issues": issues,
            "parse_failures_count": sum(a.parse_failures for a in summary.audits),
            # A lockfile without a complete report was not audited, whatever
            # the other lockfiles found.
            "execution_failed": failure is not None,
        }
        if summary.not_audited:
            return ToolResult.from_outcome(
                ToolOutcome(
                    execution=TimedOut(summary.text()),
                    issues_count=len(issues),
                ),
                failure_besides_timeout=failure,
                **payload,
            )
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(
                    success=not issues and not denied and failure is None,
                    # A lockfile that was not audited leaves inspection
                    # unknown, as for every fail-closed check.
                    inspected_files=None if failure else True,
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
