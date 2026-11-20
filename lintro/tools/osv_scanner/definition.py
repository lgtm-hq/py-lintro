"""OSV-Scanner tool definition.

OSV-Scanner is Google's vulnerability scanner that uses the Open Source
Vulnerabilities (OSV) database. It supports scanning lockfiles and SBOMs
for known vulnerabilities across multiple ecosystems including PyPI, npm,
Go, Rust, Ruby, PHP, .NET, Java, and more.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, NoFiles, TimedOut, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.osv_scanner import (
    OsvScannerIssue,
    classify_suppressions,
    parse_osv_scanner_output,
    parse_suppressions,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.execution_preparation import (
    get_effective_timeout,
    verify_tool_version,
)
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.incomplete_run import (
    incomplete_run_result,
)
from lintro.tools.core.option_validators import validate_bool, validate_positive_int
from lintro.tools.osv_scanner.extraction_log import (
    RESOLUTION_NOTE_SUMMARY,
    StderrReport,
    read_stderr,
    resolution_note,
)
from lintro.tools.osv_scanner.report import ReportCheck, check_report
from lintro.utils.path_filtering import resolve_exclude_anchors, should_exclude_path
from lintro.utils.tool_metadata import ToolNote, with_tool_notes

# Constants
OSV_SCANNER_DEFAULT_TIMEOUT: int = 120  # Network operations can be slow

# Placeholder source path used by the parser when a result carries no source.
# It is not a real filesystem path and must never be exclusion-matched.
_UNKNOWN_SOURCE: str = "lockfile"

#: Output of a scan that found no lockfile or manifest to scan (#3352).
OSV_NO_SOURCES_MESSAGE: str = (
    "No package sources found: OSV-Scanner found no lockfile or manifest to scan."
)

#: Flags on every osv-scanner scan. At ``--verbosity error`` a scan that went
#: well prints nothing on standard error, so anything printed is an error
#: (see :mod:`lintro.tools.osv_scanner.extraction_log`). Call analysis (on by
#: default for Go only) is off: lintro does not read its result, and it fails
#: when Go or govulncheck cannot run. Both flags exist in osv-scanner 2.7.1,
#: the minimum lintro enforces.
_SCAN_FLAGS: tuple[str, ...] = (
    "--recursive",
    "--format",
    "json",
    "--verbosity",
    "error",
    "--no-call-analysis=all",
)

#: Options osv-scanner has that would undo :data:`_SCAN_FLAGS`. lintro passes
#: no tool option to osv-scanner, so one of these would be silently ignored;
#: it is refused instead.
_FIXED_OPTIONS: frozenset[str] = frozenset(
    {"verbosity", "call_analysis", "no_call_analysis"},
)

#: osv-scanner's exit status when it found nothing to scan.
_NO_SOURCES_EXIT: int = 128

#: osv-scanner's exit status for a scan that found vulnerabilities.
_VULNERABILITIES_FOUND_EXIT: int = 1

#: osv-scanner's exit status when it logged an error and found no
#: vulnerability; an extraction error alone is enough to set it.
_LOGGED_ERROR_EXIT: int = 127


@register_tool
@dataclass
class OsvScannerPlugin(BaseToolPlugin):
    """OSV-Scanner vulnerability scanning plugin.

    This plugin integrates OSV-Scanner with Lintro for scanning lockfiles
    for known vulnerabilities across multiple ecosystems.

    Unlike other tool plugins, osv-scanner handles its own file discovery
    via --recursive, so file_patterns is empty and check() bypasses the
    standard file discovery pipeline. Because that discovery cannot be
    steered from the outside, lintro's resolved exclusion set
    (``.lintro-ignore`` plus ``--exclude``) is applied to the reported
    lockfile paths instead — see :meth:`filter_excluded_issues` (#1725).

    Its output is decoded with replacement, so bytes that are not UTF-8 still
    leave standard error readable and quoted in an incomplete run.
    """

    def _subprocess_decode_errors(self) -> str | None:
        """Decode osv-scanner's output with replacement characters.

        Returns:
            ``"replace"``, so stray bytes never hide the text around them.
        """
        return "replace"

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="osv_scanner",
            description=(
                "Google's vulnerability scanner using the OSV database "
                "for multi-ecosystem dependency scanning"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY,
            file_patterns=[],
            claims=[
                Claim(
                    patterns=[],
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=False,
            native_configs=[".osv-scanner.toml"],
            version_command=["osv-scanner", "--version"],
            min_version=get_min_version(ToolName.OSV_SCANNER),
            default_options={
                "timeout": OSV_SCANNER_DEFAULT_TIMEOUT,
                "check_suppressions": True,
            },
            default_timeout=OSV_SCANNER_DEFAULT_TIMEOUT,
        )

    def set_options(self, **kwargs: Any) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Options to set, including timeout and
                check_suppressions.

        Raises:
            ValueError: If an option would change a flag lintro relies on.
        """
        fixed = sorted(_FIXED_OPTIONS.intersection(kwargs))
        if fixed:
            raise ValueError(
                f"osv_scanner option(s) {', '.join(fixed)} cannot be set: lintro "
                f"always runs osv-scanner with {' '.join(_SCAN_FLAGS)}",
            )
        if "timeout" in kwargs:
            validate_positive_int(kwargs["timeout"], "timeout")
        if "check_suppressions" in kwargs:
            validate_bool(kwargs["check_suppressions"], "check_suppressions")
        super().set_options(**kwargs)

    def _build_command(self, scan_root: Path) -> list[str]:
        """Build the osv-scanner scan command.

        Uses --recursive to let osv-scanner discover lockfiles itself,
        rather than maintaining a separate list of file patterns, and the
        rest of :data:`_SCAN_FLAGS`. Passes --config explicitly because
        osv-scanner's auto-discovery does not work reliably in --recursive
        mode.

        Args:
            scan_root: Root directory to scan recursively.

        Returns:
            Command list for running osv-scanner with JSON output.
        """
        cmd = [*self._get_executable_command("osv-scanner"), "scan", *_SCAN_FLAGS]

        config = self._find_config_file(scan_root)
        if config is not None:
            cmd.extend(["--config", str(config)])

        cmd.append(str(scan_root))
        return cmd

    def _build_probe_command(self, scan_root: Path) -> list[str]:
        """Build an osv-scanner command that ignores all suppressions.

        Uses --config /dev/null to disable .osv-scanner.toml so the
        scan reports all vulnerabilities, including suppressed ones.
        This "probe" output is used to detect stale suppressions.

        Args:
            scan_root: Root directory to scan recursively.

        Returns:
            Command list for running osv-scanner without suppressions.
        """
        return [
            *self._get_executable_command("osv-scanner"),
            "scan",
            *_SCAN_FLAGS,
            "--config",
            os.devnull,
            str(scan_root),
        ]

    @staticmethod
    def _payload_has_valid_results(payload: object) -> bool:
        """Return whether an osv-scanner payload is a valid scan report.

        A well-formed osv-scanner JSON report is an object whose ``results``
        key holds a list (possibly empty). Error-shaped payloads such as
        ``{"error": ...}`` — or payloads whose ``results`` is missing or not a
        list — are not valid scan reports. For a security gate such a payload
        must be treated as a scan failure rather than a clean pass (see #1028).

        Args:
            payload: Parsed JSON object extracted from osv-scanner stdout.

        Returns:
            True when ``payload`` is a dict whose ``results`` value is a list.
        """
        return isinstance(payload, dict) and isinstance(
            payload.get("results"),
            list,
        )

    @staticmethod
    def _find_config_file(scan_root: Path) -> Path | None:
        """Find .osv-scanner.toml by walking up from the scan root.

        Matches osv-scanner's own config resolution: looks for the file
        in the scan root and each parent directory up to the filesystem
        root.

        Args:
            scan_root: Directory to start searching from.

        Returns:
            Path to the config file, or None if not found.
        """
        current = scan_root.resolve()
        for directory in [current, *current.parents]:
            config = directory / ".osv-scanner.toml"
            if config.is_file():
                return config
        return None

    def _resolve_scan_root(self, paths: list[str]) -> Path:
        """Resolve the scan root from input paths.

        Args:
            paths: Input file or directory paths.

        Returns:
            Common ancestor directory for all paths.
        """
        resolved: list[Path] = []
        for raw_path in paths:
            p = Path(raw_path).resolve()
            resolved.append(p if p.is_dir() else p.parent)

        if len(resolved) == 1:
            return resolved[0]

        try:
            return Path(os.path.commonpath([str(p) for p in resolved]))
        except ValueError:
            return resolved[0]

    def _active_exclude_patterns(self) -> list[str]:
        """Return the resolved exclusion patterns for this run.

        ``self.exclude_patterns`` is populated by the base plugin from the
        built-in defaults plus ``.lintro-ignore`` entries, and extended with
        ``--exclude`` patterns by ``set_options``. Both mechanisms therefore
        feed the same list, which is what makes them behave identically here
        (see #1725).

        Returns:
            Non-empty, stripped exclusion patterns.
        """
        return [p.strip() for p in self.exclude_patterns if p and p.strip()]

    def filter_excluded_issues(
        self,
        issues: list[OsvScannerIssue],
        paths: list[str],
        scan_root: Path | None = None,
    ) -> list[OsvScannerIssue]:
        """Drop issues whose lockfile is covered by the exclusion set.

        osv-scanner performs its own recursive lockfile discovery, so lintro
        cannot hand it a pre-filtered target list without re-implementing (and
        eventually falling behind) that discovery for every ecosystem. Instead
        the resolved exclusion set is applied to the reported source paths,
        which honours both directory patterns (``.claude``) and file patterns
        (``bun.lock``, ``*.lock``) with the same gitignore semantics used for
        file discovery (#1725).

        Args:
            issues: Parsed osv-scanner issues.
            paths: Input paths for the run, used to anchor relative patterns.
            scan_root: Directory osv-scanner ran in. Reported source paths are
                relative to it, so relative sources are resolved against it
                rather than lintro's own working directory (#1725).

        Returns:
            Issues whose source path is not excluded.
        """
        patterns = self._active_exclude_patterns()
        if not patterns or not issues:
            return list(issues)

        anchors = resolve_exclude_anchors(paths)
        kept: list[OsvScannerIssue] = []
        for issue in issues:
            if self._source_is_excluded(
                issue.file or "",
                patterns=patterns,
                anchors=anchors,
                scan_root=scan_root,
            ):
                logger.debug(
                    f"[osv-scanner] Excluding finding from ignored path: {issue.file}",
                )
                continue
            kept.append(issue)
        return kept

    @staticmethod
    def _source_is_excluded(
        source: str,
        *,
        patterns: list[str],
        anchors: tuple[str, ...],
        scan_root: Path | None,
    ) -> bool:
        """Return whether the exclusion set covers one reported source path.

        Findings and extraction errors go through this one check, so an
        error is dropped exactly when a finding for the same file would be.

        Args:
            source: The source path osv-scanner reported.
            patterns: The resolved exclusion patterns.
            anchors: Directories the patterns are relative to.
            scan_root: Directory osv-scanner ran in.

        Returns:
            True when the source is a real path the patterns exclude.
        """
        if not patterns or not source or source == _UNKNOWN_SOURCE:
            return False
        # osv-scanner runs with cwd=scan_root and reports sources relative
        # to it. should_exclude_path resolves a relative path with
        # os.path.abspath, which uses lintro's own working directory — so
        # scanning a project elsewhere would anchor the match on the wrong
        # tree and keep or drop the wrong findings (#1725).
        resolved = source
        if scan_root is not None and not os.path.isabs(source):
            resolved = os.path.join(str(scan_root), source)
        return should_exclude_path(
            path=resolved,
            exclude_patterns=patterns,
            anchors=anchors,
        )

    def doc_url(self, code: str) -> str | None:
        """Return OSV vulnerability database URL for the given ID.

        Args:
            code: Vulnerability ID (e.g., "GHSA-xxxx", "CVE-xxxx", "PYSEC-xxxx").

        Returns:
            URL to the OSV vulnerability page, or None if code is empty.
        """
        if not code:
            return None
        return DocUrlTemplate.OSV.format(code=code)

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Scan for known vulnerabilities using osv-scanner --recursive.

        Bypasses the standard file discovery pipeline since osv-scanner
        discovers lockfiles itself. Only does version checking and
        options merging before running the scan.

        Args:
            paths: List of file or directory paths to scan.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with scan results.
        """
        if not paths:
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output="No paths to check.",
            )

        # Version check
        version_result = verify_tool_version(self.definition)
        if version_result is not None:
            return version_result

        # Merge options
        merged_options = dict(self.options)
        merged_options.update(options)
        timeout = get_effective_timeout(
            timeout=None,
            options=merged_options,
            default_timeout=self.definition.default_timeout,
        )

        scan_root = self._resolve_scan_root(paths)
        cmd = self._build_command(scan_root)
        logger.debug(
            f"[osv-scanner] Running: {' '.join(cmd[:10])}... (cwd={scan_root})",
        )

        try:
            # osv-scanner returns non-zero when vulnerabilities exist
            proc = self._run_subprocess_result(
                cmd,
                timeout=timeout,
                cwd=str(scan_root),
            )
        except subprocess.TimeoutExpired:
            return ToolResult.from_outcome(
                ToolOutcome(
                    execution=TimedOut(f"OSV-Scanner timed out after {timeout}s"),
                ),
                name=self.definition.name,
            )

        # Parse the JSON report from stdout only; stderr may carry human-readable
        # log lines that must not corrupt JSON parsing (see #1043).
        parsed_issues = parse_osv_scanner_output(proc.stdout)
        # Apply .lintro-ignore / --exclude patterns to the reported lockfile
        # paths. osv-scanner does its own discovery and has no equivalent
        # ignore mechanism, so exclusions are enforced here (#1725).
        issues = self.filter_excluded_issues(
            issues=parsed_issues,
            paths=paths,
            scan_root=scan_root,
        )
        # A run without a complete verdict never reaches the scan result
        # below: for a security scanner an unknown result must never read as
        # a clean pass (#1028, #1044, #3352).
        report = self._read_stderr(
            # Not the combined output, whose report lines are not log lines.
            stderr=proc.stderr or ("" if proc.stdout.strip() else proc.output),
            paths=paths,
            scan_root=scan_root,
        )
        gap = self._gap_result(
            proc=proc,
            parsed_issues=parsed_issues,
            issues=issues,
            report=report,
        )
        if gap is not None:
            return gap

        # Exit 1 is osv-scanner's "vulnerabilities found". When every finding
        # lived under an excluded path the scan is clean for lintro (#1725).
        success = proc.success or not issues

        # Determine overall success: subprocess must succeed AND no issues
        # found.
        overall_success = success and len(issues) == 0

        # Suppression staleness check
        suppression_metadata = self._check_suppression_staleness(
            scan_root=scan_root,
            timeout=timeout,
            options=merged_options,
            paths=paths,
        )

        # The note travels in metadata, so ``output`` stays the JSON report.
        metadata = with_tool_notes(
            suppression_metadata,
            [
                ToolNote(summary=RESOLUTION_NOTE_SUMMARY, message=note)
                for note in (resolution_note(report.notes, scan_root),)
                if note
            ],
        )
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=overall_success, inspected_files=True),
                issues_count=len(issues),
            ),
            name=self.definition.name,
            output=proc.stdout if issues else None,
            issues=issues,
            metadata=metadata,
            parse_failures_count=0,
        )

    def _gap_result(
        self,
        *,
        proc: SubprocessResult,
        parsed_issues: list[OsvScannerIssue],
        issues: list[OsvScannerIssue],
        report: StderrReport,
    ) -> ToolResult | None:
        """Build the result for a run that did not finish a complete scan.

        A run is complete only when stdout is exactly one scan report (see
        :mod:`lintro.tools.osv_scanner.report`) and either nothing was logged
        at error level (exit 0, or exit 1 with vulnerabilities in the report)
        or everything logged is a resolution note or an error for an excluded
        file (exit 0, 1 or 127).

        Args:
            proc: The finished osv-scanner process.
            parsed_issues: Findings parsed from the report, before exclusions.
            issues: The findings left after exclusions.
            report: What osv-scanner printed on standard error.

        Returns:
            A no-files result when there was nothing to scan, an incomplete
            run result when the scan was not complete, or None when the report
            is a complete verdict.
        """
        # "No package sources found" alone is no work, not a scan (#3352).
        # When a source could not be read the same line is printed beside its
        # extraction error, and that run is incomplete instead.
        if (
            proc.returncode == _NO_SOURCES_EXIT
            and not parsed_issues
            and not report.notes
            and report.only_no_sources
        ):
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output=OSV_NO_SOURCES_MESSAGE,
                parse_failures_count=0,
            )
        whole = check_report(proc.stdout)
        parse_failures: int | None = 0 if whole.ok else 1
        if not proc.stdout.strip():
            parse_failures = None
        reason = self._gap_reason(
            proc=proc,
            whole=whole,
            found=bool(parsed_issues),
            report=report,
        )
        if reason is None:
            return None
        if report.printed:
            reason = "\n".join((reason, *report.printed))
        return incomplete_run_result(
            name=self.definition.name,
            reason=reason,
            issues=issues,
            parse_failures=parse_failures,
        )

    @staticmethod
    def _gap_reason(
        *,
        proc: SubprocessResult,
        whole: ReportCheck,
        found: bool,
        report: StderrReport,
    ) -> str | None:
        """Name why a run is not a complete scan, before osv-scanner's text.

        Args:
            proc: The finished osv-scanner process.
            whole: Whether stdout is exactly one scan report.
            found: Whether the report lists any vulnerability.
            report: What osv-scanner printed on standard error.

        Returns:
            The reason, or None when the run is a complete verdict. The
            caller appends osv-scanner's error lines verbatim.
        """
        code = proc.returncode
        if not whole.ok:
            if not proc.stdout.strip():
                return f"OSV-Scanner printed no report (exit code {code})."
            return whole.reason
        if report.errors:
            return (
                f"OSV-Scanner reported an error (exit code {code}), so the scan "
                "is incomplete. Its standard error:"
            )
        if code == 0 or (code == _VULNERABILITIES_FOUND_EXIT and found):
            return None
        if code == _LOGGED_ERROR_EXIT and report.explained:
            return None
        return (
            f"OSV-Scanner exited with an error (exit code {code}) that nothing "
            "it printed explains, so the scan is incomplete."
        )

    def _read_stderr(
        self,
        *,
        stderr: str,
        paths: list[str],
        scan_root: Path,
    ) -> StderrReport:
        """Sort what osv-scanner printed on standard error.

        See :mod:`lintro.tools.osv_scanner.extraction_log`. A message naming
        only regular files under excluded paths is dropped, as a finding
        there would be (#1725).

        Args:
            stderr: What osv-scanner printed on standard error.
            paths: Input paths for the run, used to anchor exclude patterns.
            scan_root: Directory osv-scanner ran in.

        Returns:
            The resolution notes and the error lines.
        """
        patterns = self._active_exclude_patterns()
        anchors = resolve_exclude_anchors(paths) if patterns else ()
        return read_stderr(
            stderr,
            scan_root,
            is_excluded=lambda source: self._source_is_excluded(
                source,
                patterns=patterns,
                anchors=anchors,
                scan_root=scan_root,
            ),
        )

    def _check_suppression_staleness(
        self,
        scan_root: Path,
        timeout: float,
        options: dict[str, object],
        paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a probe scan to classify suppression entries.

        Skipped when check_suppressions is disabled or no config file
        with suppressions exists.

        Args:
            scan_root: Root directory for the scan.
            timeout: Timeout for subprocess execution.
            options: Merged runtime options.
            paths: Input paths for the run, used to anchor exclude patterns so
                probe findings from ignored trees cannot keep a suppression
                looking active (#1725).

        Returns:
            Suppression classifications, or an empty dict when unavailable.
        """
        check = options.get(
            "check_suppressions",
            self.options.get("check_suppressions", True),
        )
        if not check:
            return {}

        config_path = self._find_config_file(scan_root)
        if config_path is None:
            return {}

        entries = parse_suppressions(config_path)
        if not entries:
            return {}

        # Run osv-scanner without suppressions to see all vulnerabilities
        probe_cmd = self._build_probe_command(scan_root)
        try:
            probe = self._run_subprocess_result(
                probe_cmd,
                timeout=timeout,
                cwd=str(scan_root),
            )
        except subprocess.TimeoutExpired:
            logger.debug("[osv-scanner] Probe scan timed out, skipping staleness check")
            return {}

        parsed_probe_issues = parse_osv_scanner_output(probe.stdout)

        # Decide "unreadable probe" on the UNFILTERED parse. osv-scanner exits
        # non-zero whenever vulnerabilities exist, so probe.success is False
        # both when the probe produced nothing readable and when it found
        # vulnerabilities that are all under excluded paths. Testing the
        # filtered list conflates the two, and suppressions covering only
        # excluded findings would never be classified as stale — the opposite
        # of the exclusion contract (#1725).
        if not probe.success and not parsed_probe_issues:
            logger.debug(
                "[osv-scanner] Probe scan failed with no parseable output, "
                "skipping staleness check",
            )
            return {}

        probe_issues = self.filter_excluded_issues(
            issues=parsed_probe_issues,
            paths=list(paths) if paths else [str(scan_root)],
            scan_root=scan_root,
        )

        probe_vuln_ids = {issue.vuln_id for issue in probe_issues}

        classified = classify_suppressions(entries, probe_vuln_ids)

        return {
            "suppressions": [
                {
                    "id": c.entry.id,
                    "ignore_until": c.entry.ignore_until.isoformat(),
                    "reason": c.entry.reason,
                    "status": c.status.value,
                }
                for c in classified
            ],
        }

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """OSV-Scanner cannot fix vulnerabilities, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: OSV-Scanner does not support fixing issues.
        """
        raise NotImplementedError(
            "OSV-Scanner cannot automatically fix vulnerabilities. "
            "Update affected packages to their fixed versions.",
        )
