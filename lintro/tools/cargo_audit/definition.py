"""Cargo-audit tool definition.

Cargo-audit is a security tool for Rust projects that scans Cargo.lock
for dependencies with known security vulnerabilities from the RustSec
advisory database.
"""

from __future__ import annotations

import subprocess  # nosec B404 - used safely with shell disabled
import time
from dataclasses import dataclass, replace
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

# Constants for cargo-audit configuration
CARGO_AUDIT_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow
CARGO_AUDIT_LOCKFILE: str = "Cargo.lock"
CARGO_AUDIT_FILE_PATTERNS: list[str] = [CARGO_AUDIT_LOCKFILE]
# Lockfiles under these directories belong to third-party or generated crates:
# ``cargo vendor`` copies dependencies into ``vendor/`` and ``cargo package``
# leaves packaged copies under ``target/``. Neither is a resolution the project
# builds with, so auditing them reports findings nobody here can fix.
CARGO_AUDIT_SKIP_DIRS: frozenset[str] = frozenset({"vendor", "target"})


def _is_third_party(lock_root: Path, *, base: Path) -> bool:
    """Tell whether a lockfile directory lies inside a vendored or build tree.

    Args:
        lock_root: Directory holding a Cargo.lock.
        base: Execution directory; only components below it are considered,
            so a project that itself lives under a ``target`` or ``vendor``
            folder still audits its own lockfile.

    Returns:
        True when a directory between ``base`` and ``lock_root`` is skipped.
        False for a lockfile at or above ``base``, or outside it, since no
        part of its path lies below the scan root.
    """
    try:
        parts = lock_root.relative_to(base).parts
    except ValueError:
        return False
    return any(part in CARGO_AUDIT_SKIP_DIRS for part in parts)


def _find_lock_roots(paths: list[str], *, base: Path) -> list[Path]:
    """Return every directory owning a Cargo.lock the given paths belong to.

    Each lockfile is a separate dependency resolution, so a file set spanning
    unrelated crates needs one audit per lockfile. A workspace keeps a single
    lockfile at its root, so its members all resolve to that one directory.

    Args:
        paths: List of file paths to search from.
        base: Execution directory, used to recognise vendored and build trees.

    Returns:
        The distinct directories holding the nearest Cargo.lock above each
        path, sorted for a deterministic run order. Lockfiles inside
        ``vendor/`` or ``target/`` are left out. Empty when none is found.
    """
    roots: set[Path] = set()
    for raw_path in paths:
        current = Path(raw_path).resolve()
        # If it's a file, start from its parent
        if current.is_file():
            current = current.parent
        # Search upward for Cargo.lock
        for candidate in [current, *current.parents]:
            if (candidate / CARGO_AUDIT_LOCKFILE).exists():
                roots.add(candidate)
                break
    return sorted(root for root in roots if not _is_third_party(root, base=base))


@dataclass(frozen=True)
class _AuditRun:
    """The interpreted result of auditing one Cargo.lock root.

    Attributes:
        issues: Vulnerabilities parsed from the report.
        success: Whether the command ran and produced a usable report.
        parse_failed: Whether the report was missing or schema-invalid.
        output: Combined command output, kept for display.
    """

    issues: list[CargoAuditIssue]
    success: bool
    parse_failed: bool
    output: str


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
            timeout: Overall timeout in seconds, shared by every lockfile
                audited in one check.
            **kwargs: Additional options.
        """
        validate_positive_int(timeout, "timeout")

        options = filter_none_options(timeout=timeout)
        super().set_options(**options, **kwargs)

    def _build_command(self, *, fetch: bool = True) -> list[str]:
        """Build the cargo-audit command.

        Args:
            fetch: Whether cargo-audit may update the advisory database. Once
                one lockfile has been audited the database is current, so
                later lockfiles reuse it instead of fetching it again.

        Returns:
            Command list for running cargo-audit with JSON output.
        """
        cmd = ["cargo", "audit", "--json"]
        if not fetch:
            cmd.append("--no-fetch")
        return cmd

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
            return ctx

        # Audit every lockfile the filtered file list belongs to.
        lock_roots = _find_lock_roots(ctx.files, base=Path(ctx.cwd).resolve())
        if not lock_roots:
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output="No Cargo.lock found; skipping cargo-audit.",
            )

        # The timeout is one budget for the whole check, not one per lockfile.
        deadline = time.monotonic() + ctx.timeout
        runs: list[_AuditRun] = []
        for lock_root in lock_roots:
            remaining = deadline - time.monotonic()
            # A parsed report proves the advisory database is in place.
            fetched = any(not run.parse_failed for run in runs)
            run = (
                self._audit_root(lock_root, timeout=remaining, fetch=not fetched)
                if remaining > 0
                else None
            )
            if run is None:
                # The scan is incomplete; keep what earlier roots found.
                timed_out_issues = [i for r in runs for i in r.issues]
                return ToolResult.from_outcome(
                    ToolOutcome(
                        execution=TimedOut(
                            f"cargo-audit timed out: the {ctx.timeout}s budget "
                            f"ran out auditing {lock_root / CARGO_AUDIT_LOCKFILE}",
                        ),
                        issues_count=len(timed_out_issues),
                    ),
                    name=self.definition.name,
                    issues=timed_out_issues,
                )
            runs.append(run)

        issues = [issue for run in runs for issue in run.issues]
        # Every root must run and report cleanly, with no vulnerabilities.
        # cargo-audit exits non-zero both for findings and for execution
        # errors, so a failed run with nothing parsed is never a clean scan.
        overall_success = all(run.success for run in runs) and not issues

        # Show a root's output when it has issues or failed without them.
        shown = [run.output for run in runs if run.issues or not run.success]

        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=overall_success, inspected_files=True),
                issues_count=len(issues),
            ),
            name=self.definition.name,
            output="\n".join(shown) if shown else None,
            issues=issues,
            parse_failures_count=sum(1 for run in runs if run.parse_failed),
        )

    def _audit_root(
        self,
        lock_root: Path,
        *,
        timeout: float,
        fetch: bool,
    ) -> _AuditRun | None:
        """Audit the Cargo.lock in one directory.

        Args:
            lock_root: Directory holding the Cargo.lock to audit. cargo-audit
                runs there so it also picks up that project's
                ``.cargo/audit.toml``.
            timeout: Time left in the check's budget, in seconds.
            fetch: Whether cargo-audit may update the advisory database.

        Returns:
            The interpreted run, or ``None`` when it timed out.
        """
        cmd = self._build_command(fetch=fetch)
        try:
            proc = self._run_subprocess_result(
                cmd,
                timeout=timeout,
                cwd=str(lock_root),
            )
        except subprocess.TimeoutExpired:
            return None

        # cargo-audit writes its JSON report to stdout; parse stdout only so a
        # stderr warning cannot corrupt parsing (see #1043). ``output``
        # (combined) is retained for display.
        payload = extract_cargo_audit_payload(proc.stdout)
        # Each finding names its own lockfile, so the same advisory in two
        # lockfiles stays two findings in reports and baselines.
        lockfile = str(lock_root / CARGO_AUDIT_LOCKFILE)
        issues = [
            replace(issue, file=lockfile)
            for issue in parse_cargo_audit_output(proc.stdout, data=payload)
        ]
        # A missing or schema-invalid report cannot establish a clean scan,
        # even when the subprocess succeeds (see #1044 and #3003).
        parse_failed = payload is None
        return _AuditRun(
            issues=issues,
            success=proc.success and not parse_failed,
            parse_failed=parse_failed,
            output=proc.output,
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
