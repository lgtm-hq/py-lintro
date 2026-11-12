#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Fail dogfooding CI when an enabled tool silently skips.

A SKIP is indistinguishable from a PASS in job status, so a wrapped tool can
be dead for weeks (missing binary) or forever (missing config) without anyone
noticing. This gate parses lintro's structured JSON output
(``lintro chk --output-format json``) and exits non-zero when any enabled tool
reports ``skipped: true`` or ``status: unavailable`` / ``unavailable: true``
for a reason that is not covered by the committed
allowlist (``scripts/ci/dogfood-skip-allowlist.yaml``).

Skip reasons are classified into one of:

- ``binary_missing`` — the tool binary is absent or its version check failed.
  This is an *image bug*: the tool should be installed in the CI tools image.
  It is **never permanently allowlistable** and may only be tolerated via an
  ``interim`` entry that carries a tracking issue (which emits a loud warning).
- ``no_config`` — the tool has no resolvable configuration in the repo, so it
  self-skips (e.g. stylelint/vale/commitlint). Allowlistable via the file.
- ``opt_in_disabled`` — an opt-in tool (idiom-review) is disabled by default.
  Allowlistable via the file.
- ``other`` — an unrecognised skip reason. Not permanently allowlistable
  (like ``binary_missing``); it must be explicitly tolerated via an
  ``interim`` entry with a tracking issue.

An unavailable row that carries ``probe_failure: probe_timeout`` (#2337) is a
version probe that timed out on its first attempt and on the retry. The binary
exists, so a slow runner can produce it for a working tool. It is still an
image-dependency failure (``binary_missing``) for allowlist purposes, with one
exception (#3383): it is absorbed, as a warning that names the timeout and its
``probe_seconds``, when the same tool reported a verdict (``status`` ``ok`` or
``failed``, and neither ``execution_failed`` nor ``timed_out``) in a
``--sibling-report`` from another attempt of the same run. Siblings are read
only when such a row exists, and an unreadable one counts as no evidence.
Without that evidence it fails like any other unavailable row, and when every
violation is such a timeout the checker says so on its ``gate-output:`` lines
so CI can classify the failure as infrastructure. ``binary_missing`` rows and
unavailable rows with no ``probe_failure`` are never absorbed.

Usage:
    python3 scripts/ci/check-dogfood-skips.py \
        --report results.json \
        --allowlist scripts/ci/dogfood-skip-allowlist.yaml

    # or read the JSON report from stdin
    lintro chk --output-format json . | \
        python3 scripts/ci/check-dogfood-skips.py \
            --allowlist scripts/ci/dogfood-skip-allowlist.yaml

    # absorb a probe timeout only when another attempt has a verdict
    python3 scripts/ci/check-dogfood-skips.py \
        --report results.json \
        --sibling-report other-attempt.json

Output:
    Once a verdict (0 or 1) is reached the checker also prints two
    ``gate-output: key=value`` lines on stdout for the CI wrapper:
    ``probe-timeout-flake`` (``true`` only when every violation is a probe
    timeout with no sibling verdict) and ``probe-timed-out-tools``.

Exit codes:
    0 — no non-allowlisted skips (gate passes)
    1 — one or more non-allowlisted skips (gate fails)
    2 — usage / configuration error (bad args, unreadable report or allowlist,
        or an allowlist that violates the schema)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class SkipClass(StrEnum):
    """Classification of a tool skip reason."""

    BINARY_MISSING = "binary_missing"
    NO_CONFIG = "no_config"
    OPT_IN_DISABLED = "opt_in_disabled"
    OTHER = "other"


# Classes that a *permanent* allowlist entry is allowed to cover. A missing
# binary is an image bug, so it is deliberately excluded here — it may only be
# tolerated as a tracked ``interim`` entry.
PERMANENT_ALLOWLISTABLE: frozenset[SkipClass] = frozenset(
    {SkipClass.NO_CONFIG, SkipClass.OPT_IN_DISABLED},
)

# ``probe_failure`` value lintro writes when the version probe timed out on
# both attempts (#2337). Mirrors ``lintro.enums.probe_failure``; this script
# runs inside the image with only the stdlib and PyYAML, so it cannot import
# lintro.
PROBE_TIMEOUT = "probe_timeout"

# Statuses that prove a tool ran and returned a verdict. ``skipped`` and
# ``unavailable`` are not verdicts, and a row with no status is not evidence.
VERDICT_STATUSES: frozenset[str] = frozenset({"ok", "failed"})

# Prefix of the machine-readable lines the CI wrapper copies to GITHUB_OUTPUT.
GATE_OUTPUT_PREFIX = "gate-output: "


class AllowlistError(ValueError):
    """Raised when the allowlist file is malformed or violates the schema."""


def normalize_tool_name(name: str) -> str:
    """Return a canonical key for a tool name.

    lintro emits some tools with hyphens (``idiom-review``, ``svelte-check``)
    and others with underscores (``pip_audit``, ``osv_scanner``). Normalising
    both to a single form lets the allowlist match regardless of the spelling
    used in the file.

    Args:
        name: Tool name as it appears in JSON output or the allowlist.

    Returns:
        Lower-cased name with hyphens folded to underscores.
    """
    return name.strip().lower().replace("-", "_")


def classify_skip_reason(reason: str | None) -> SkipClass:
    """Classify a skip reason string into a :class:`SkipClass`.

    Matching is case-insensitive and pattern-based, checked in priority order
    so the most dangerous class (a missing binary) wins.

    Args:
        reason: The ``skip_reason`` string from a lintro tool result.

    Returns:
        The classified :class:`SkipClass`. An empty/None reason is treated as
        ``other`` (an unexplained skip is never silently tolerated).
    """
    text = (reason or "").lower()
    if not text:
        return SkipClass.OTHER

    binary_patterns = (
        r"\bfailed to run version check\b",
        r"\bversion check failed\b",
        r"\bno such file or directory\b",
        r"\bcommand not found\b",
        r"\bnot found in path\b",
        r"\bis not installed\b",
        r"\bnot installed\b",
        r"\bexecutable not found\b",
    )
    if any(re.search(pattern, text) for pattern in binary_patterns):
        return SkipClass.BINARY_MISSING

    opt_in_markers = (
        "disabled by default",
        "opt-in",
        "opt in",
        "disabled (opt-in",
    )
    if any(marker in text for marker in opt_in_markers):
        return SkipClass.OPT_IN_DISABLED

    config_patterns = (
        r"\bno\b.*\bconfig(?:uration)?\b.*\b(?:found|provided)\b",
        r"\bno\b.*\bconfig(?:uration)?\b",
        r"\bconfig(?:uration)?(?: file)?\b.*\b(?:not found|missing|required)\b",
        r"\b\.vale\.ini\b.*\b(?:not found|missing|required)\b",
    )
    if any(re.search(pattern, text) for pattern in config_patterns):
        return SkipClass.NO_CONFIG

    return SkipClass.OTHER


@dataclass(frozen=True)
class AllowlistEntry:
    """A single allowlist rule for a tool skip."""

    tool: str
    reason_class: SkipClass
    rationale: str
    interim: bool
    issue: int | None = None


@dataclass
class Allowlist:
    """A parsed skip allowlist, keyed by normalized tool name."""

    entries: dict[str, AllowlistEntry] = field(default_factory=dict)

    def get(self, tool: str) -> AllowlistEntry | None:
        """Return the entry for ``tool`` (normalized), or None."""
        return self.entries.get(normalize_tool_name(tool))


def _parse_entry(raw: Any, *, interim: bool) -> AllowlistEntry:
    """Validate and build one :class:`AllowlistEntry` from raw YAML data.

    Args:
        raw: A single mapping from the allowlist ``allowlist``/``interim`` list.
        interim: Whether this entry came from the ``interim`` section.

    Returns:
        The validated allowlist entry.

    Raises:
        AllowlistError: If required fields are missing or invalid.
    """
    if not isinstance(raw, dict):
        raise AllowlistError(f"allowlist entry must be a mapping, got {type(raw)!r}")

    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise AllowlistError(f"allowlist entry missing a non-empty 'tool': {raw!r}")

    reason_raw = raw.get("reason_class")
    valid = ", ".join(c.value for c in SkipClass)
    if not isinstance(reason_raw, str):
        raise AllowlistError(
            f"{tool}: 'reason_class' is required (expected one of: {valid})",
        )
    try:
        reason_class = SkipClass(reason_raw)
    except ValueError as exc:
        raise AllowlistError(
            f"{tool}: invalid reason_class {reason_raw!r} (expected one of: {valid})",
        ) from exc

    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise AllowlistError(f"{tool}: 'rationale' is required and must be non-empty")

    issue = raw.get("issue")
    if issue is not None and not isinstance(issue, int):
        raise AllowlistError(f"{tool}: 'issue' must be an integer when set")

    if interim:
        # Interim tolerations are temporary and must be traceable to a
        # tracking issue so they get removed once remediated.
        if issue is None:
            raise AllowlistError(
                f"{tool}: interim entries must reference a tracking 'issue'",
            )
    elif reason_class not in PERMANENT_ALLOWLISTABLE:
        # A missing binary is an image bug and must never be masked
        # permanently; it can only be tolerated as a tracked interim entry.
        allowed = ", ".join(sorted(c.value for c in PERMANENT_ALLOWLISTABLE))
        raise AllowlistError(
            f"{tool}: reason_class {reason_class.value!r} is not permanently "
            f"allowlistable (permanent entries allow: {allowed}); move it to the "
            "'interim' section with a tracking issue",
        )

    return AllowlistEntry(
        tool=normalize_tool_name(tool),
        reason_class=reason_class,
        rationale=rationale.strip(),
        interim=interim,
        issue=issue,
    )


def load_allowlist(data: Any) -> Allowlist:
    """Build an :class:`Allowlist` from parsed YAML data.

    Args:
        data: The parsed YAML document. ``None`` (empty file) yields an empty
            allowlist.

    Returns:
        The parsed and validated allowlist.

    Raises:
        AllowlistError: If the document shape or any entry is invalid, or if a
            tool appears in more than one entry.
    """
    if data is None:
        return Allowlist()
    if not isinstance(data, dict):
        raise AllowlistError("allowlist root must be a mapping")

    entries: dict[str, AllowlistEntry] = {}
    for section, interim in (("allowlist", False), ("interim", True)):
        raw_list = data.get(section) or []
        if not isinstance(raw_list, list):
            raise AllowlistError(f"'{section}' must be a list when present")
        for raw in raw_list:
            entry = _parse_entry(raw, interim=interim)
            if entry.tool in entries:
                raise AllowlistError(
                    f"{entry.tool}: duplicate allowlist entry (each tool may "
                    "appear at most once across 'allowlist' and 'interim')",
                )
            entries[entry.tool] = entry
    return Allowlist(entries=entries)


@dataclass(frozen=True)
class SkipFinding:
    """The outcome of evaluating one skipped tool against the allowlist."""

    tool: str
    reason: str
    skip_class: SkipClass
    allowed: bool
    message: str
    is_warning: bool = False
    probe_failure: str | None = None


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    """Return the list of per-tool result dicts from a lintro JSON payload.

    Args:
        payload: The parsed ``lintro chk --output-format json`` document.

    Returns:
        The ``results`` list.

    Raises:
        ValueError: If the payload is not the expected shape.
    """
    if not isinstance(payload, dict):
        raise ValueError("lintro report must be a JSON object")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("lintro report is missing a 'results' array")
    return [entry for entry in results if isinstance(entry, dict)]


def _has_verdict(result: dict[str, Any]) -> bool:
    """Return whether a report row proves its tool ran to a verdict.

    Args:
        result: One per-tool row of a lintro JSON report.

    Returns:
        True for a row whose ``status`` is ``ok`` or ``failed`` and that is
        neither skipped, unavailable, timed out nor an execution failure.
        lintro reports every run that did not succeed as ``failed``, including
        a crash or an unparseable tool output (``execution_failed: true``) and
        a tool-execution timeout (``timed_out: true``); neither proves the
        tool answered, so neither is a verdict.
    """
    return (
        result.get("status") in VERDICT_STATUSES
        and not result.get("skipped")
        and result.get("unavailable") is not True
        and result.get("timed_out") is not True
        and result.get("execution_failed") is not True
    )


def tools_with_verdict(payloads: list[Any]) -> frozenset[str]:
    """Collect the normalized names of tools that reported a verdict.

    Args:
        payloads: Parsed lintro JSON reports from other attempts of the run.

    Returns:
        Normalized tool names with at least one verdict row.
    """
    return frozenset(
        normalize_tool_name(str(result.get("tool") or ""))
        for payload in payloads
        for result in _extract_results(payload)
        if _has_verdict(result)
    )


def _probe_seconds_text(result: dict[str, Any]) -> str:
    """Format a row's ``probe_seconds`` for a gate message.

    Args:
        result: One per-tool row of a lintro JSON report.

    Returns:
        The probe budget such as ``120s``, or ``unknown`` when absent.
    """
    seconds = result.get("probe_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return "unknown"
    return f"{seconds:g}s"


def _allowlist_finding(
    *,
    tool: str,
    reason: str,
    skip_class: SkipClass,
    label: str,
    state: str,
    entry: AllowlistEntry | None,
) -> SkipFinding:
    """Judge one skipped or unavailable row against its allowlist entry.

    Args:
        tool: Tool name as reported.
        reason: Skip or unavailable reason (may be empty).
        skip_class: Class the allowlist entry must match.
        label: Class text shown in messages (adds probe evidence when known).
        state: ``skipped`` or ``unavailable``.
        entry: The tool's allowlist entry, or None.

    Returns:
        The finding for this row.
    """
    shown_reason = reason or "<none>"
    if entry is None:
        return SkipFinding(
            tool=tool,
            reason=reason,
            skip_class=skip_class,
            allowed=False,
            message=(
                f"{tool}: {state} ({label}) with no allowlist "
                f"entry — reason: {shown_reason}"
            ),
        )

    if entry.reason_class != skip_class:
        return SkipFinding(
            tool=tool,
            reason=reason,
            skip_class=skip_class,
            allowed=False,
            message=(
                f"{tool}: {state} as {label} but allowlisted for "
                f"{entry.reason_class.value} — re-triage the skip reason: "
                f"{shown_reason}"
            ),
        )

    # Allowlisted skip. Interim binary-missing tolerations are surfaced as
    # loud warnings so an image bug can never be masked silently forever.
    if entry.interim:
        issue_ref = f" (see #{entry.issue})" if entry.issue is not None else ""
        return SkipFinding(
            tool=tool,
            reason=reason,
            skip_class=skip_class,
            allowed=True,
            is_warning=True,
            message=(
                f"{tool}: interim-allowlisted skip ({label})"
                f"{issue_ref} — {entry.rationale}"
            ),
        )
    return SkipFinding(
        tool=tool,
        reason=reason,
        skip_class=skip_class,
        allowed=True,
        message=f"{tool}: allowlisted skip ({label}) — {entry.rationale}",
    )


def evaluate_skips(
    payload: Any,
    allowlist: Allowlist,
    *,
    sibling_payloads: list[Any] | None = None,
) -> list[SkipFinding]:
    """Evaluate skipped and unavailable tools against the allowlist.

    Args:
        payload: Parsed lintro JSON report.
        allowlist: The parsed allowlist.
        sibling_payloads: Parsed reports from other attempts of the same run.
            A ``probe_timeout`` row is absorbed only when one of them holds a
            verdict for the same tool.

    Returns:
        One :class:`SkipFinding` per skipped or unavailable tool, in report order.
    """
    verdicts = tools_with_verdict(sibling_payloads or [])
    findings: list[SkipFinding] = []
    for result in _extract_results(payload):
        unavailable = (
            result.get("status") == "unavailable" or result.get("unavailable") is True
        )
        if not result.get("skipped") and not unavailable:
            continue
        tool = str(result.get("tool") or "unknown")
        reason = result.get("unavailable_reason" if unavailable else "skip_reason")
        reason_str = reason if isinstance(reason, str) else ""
        # Structured unavailability is an image dependency failure regardless
        # of the wording; a no-config allowlist must never mask it.
        skip_class = (
            SkipClass.BINARY_MISSING
            if unavailable
            else classify_skip_reason(reason_str)
        )
        probe_failure = result.get("probe_failure") if unavailable else None
        if probe_failure != PROBE_TIMEOUT:
            findings.append(
                _allowlist_finding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    label=skip_class.value,
                    state="unavailable" if unavailable else "skipped",
                    entry=allowlist.get(tool),
                ),
            )
            continue

        label = f"{PROBE_TIMEOUT} after {_probe_seconds_text(result)}"
        if normalize_tool_name(tool) in verdicts:
            # The binary answered in another attempt of this run, so the
            # timeout here is runner noise, not an image regression (#3383).
            findings.append(
                SkipFinding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    allowed=True,
                    is_warning=True,
                    probe_failure=PROBE_TIMEOUT,
                    message=(
                        f"{tool}: unavailable ({label}) absorbed — the same "
                        "tool reported a verdict in another attempt of this run"
                    ),
                ),
            )
            continue

        finding = _allowlist_finding(
            tool=tool,
            reason=reason_str,
            skip_class=skip_class,
            label=label,
            state="unavailable",
            entry=allowlist.get(tool),
        )
        findings.append(
            SkipFinding(
                tool=finding.tool,
                reason=finding.reason,
                skip_class=finding.skip_class,
                allowed=finding.allowed,
                is_warning=finding.is_warning,
                probe_failure=PROBE_TIMEOUT,
                message=(
                    finding.message
                    if finding.allowed
                    else f"{finding.message} (no sibling verdict for this tool)"
                ),
            ),
        )
    return findings


def has_probe_timeout(payload: Any) -> bool:
    """Return whether a report holds an unavailable ``probe_timeout`` row.

    Args:
        payload: Parsed lintro JSON report.

    Returns:
        True when sibling evidence could change the verdict. A malformed
        report returns False; :func:`evaluate_skips` reports it.
    """
    try:
        results = _extract_results(payload)
    except ValueError:
        return False
    return any(
        result.get("probe_failure") == PROBE_TIMEOUT
        and (result.get("status") == "unavailable" or result.get("unavailable") is True)
        for result in results
    )


def load_sibling_payloads(paths: list[str]) -> list[Any]:
    """Read sibling reports, dropping any that cannot serve as evidence.

    A sibling is only evidence for absorbing a probe timeout, so one that is
    missing, empty, not JSON or not a lintro report is skipped with a warning
    naming the file. The timeout it might have answered then stays fatal.

    Args:
        paths: Paths to reports from other attempts of the same run.

    Returns:
        The readable sibling reports.
    """
    payloads: list[Any] = []
    for path in paths:
        try:
            payload = _read_report(path)
            _extract_results(payload)
        except (OSError, ValueError) as exc:
            print(
                f"warning: ignoring sibling report {path} (no evidence): {exc}",
                file=sys.stderr,
            )
            continue
        payloads.append(payload)
    return payloads


def probe_timeout_only_tools(findings: list[SkipFinding]) -> list[str]:
    """Return the timed-out tools when probe timeouts are the only violations.

    Args:
        findings: Findings from :func:`evaluate_skips`.

    Returns:
        The violating tools in report order, or an empty list when there is no
        violation or any violation is not an unabsorbed probe timeout.
    """
    violations = [f for f in findings if not f.allowed]
    if not violations or any(f.probe_failure != PROBE_TIMEOUT for f in violations):
        return []
    return [f.tool for f in violations]


def _read_report(report_arg: str | None) -> Any:
    """Read and parse the lintro JSON report from a file or stdin.

    Args:
        report_arg: Path to the report file, or ``None``/``-`` for stdin.

    Returns:
        The parsed JSON document.

    Raises:
        ValueError: On unreadable or malformed input.
    """
    if report_arg in (None, "-"):
        raw = sys.stdin.read()
    else:
        raw = Path(report_arg).read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError("lintro report is empty")
    return json.loads(raw)


def _print_report(findings: list[SkipFinding]) -> None:
    """Print a human-readable summary of skip findings to stdout/stderr."""
    allowed = [f for f in findings if f.allowed and not f.is_warning]
    warnings = [f for f in findings if f.allowed and f.is_warning]
    violations = [f for f in findings if not f.allowed]

    print("Dogfood no-silent-skip gate")
    print(f"  skipped tools : {len(findings)}")
    print(f"  allowlisted   : {len(allowed)}")
    print(f"  interim/warn  : {len(warnings)}")
    print(f"  violations    : {len(violations)}")

    for finding in allowed:
        print(f"  OK    {finding.message}")
    for finding in warnings:
        print(f"  WARN  {finding.message}", file=sys.stderr)
    for finding in violations:
        print(f"  FAIL  {finding.message}", file=sys.stderr)


def _print_gate_outputs(probe_timeout_tools: list[str]) -> None:
    """Print the machine-readable lines the CI wrapper publishes.

    Args:
        probe_timeout_tools: Result of :func:`probe_timeout_only_tools`.
    """
    flake = "true" if probe_timeout_tools else "false"
    print(f"{GATE_OUTPUT_PREFIX}probe-timeout-flake={flake}")
    print(f"{GATE_OUTPUT_PREFIX}probe-timed-out-tools={','.join(probe_timeout_tools)}")


def run(
    report_arg: str | None,
    allowlist_arg: str,
    sibling_args: list[str] | None = None,
) -> int:
    """Run the gate and return the process exit code.

    Args:
        report_arg: Path to the lintro JSON report, or ``None``/``-`` for stdin.
        allowlist_arg: Path to the allowlist YAML file.
        sibling_args: Paths to reports from other attempts of the same run.

    Returns:
        Exit code: 0 (pass), 1 (violations), or 2 (usage/config error).
    """
    try:
        payload = _read_report(report_arg)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to read lintro report: {exc}", file=sys.stderr)
        return 2
    # Siblings matter only to a probe_timeout row, so a report without one
    # never reads them and a broken sibling can never fail a clean gate.
    siblings = (
        load_sibling_payloads(sibling_args or []) if has_probe_timeout(payload) else []
    )

    try:
        allowlist_data = yaml.safe_load(
            Path(allowlist_arg).read_text(encoding="utf-8"),
        )
    except (OSError, yaml.YAMLError) as exc:
        print(f"error: failed to read allowlist: {exc}", file=sys.stderr)
        return 2

    try:
        allowlist = load_allowlist(allowlist_data)
    except AllowlistError as exc:
        print(f"error: invalid allowlist: {exc}", file=sys.stderr)
        return 2

    try:
        findings = evaluate_skips(payload, allowlist, sibling_payloads=siblings)
    except ValueError as exc:
        print(f"error: malformed lintro report: {exc}", file=sys.stderr)
        return 2

    _print_report(findings)
    probe_timeout_tools = probe_timeout_only_tools(findings)
    _print_gate_outputs(probe_timeout_tools)

    violations = [f for f in findings if not f.allowed]
    if probe_timeout_tools:
        print(
            "\nGate failed: the version probe timed out for "
            f"{', '.join(probe_timeout_tools)} and no other attempt of this run "
            "reported a verdict for it. A timed-out probe is usually runner "
            "slowness, but without a verdict nothing proves the tool is "
            "installed, so the gate stays red until an attempt answers.",
            file=sys.stderr,
        )
        return 1
    if violations:
        print(
            f"\nGate failed: {len(violations)} enabled tool(s) silently skipped. "
            "Install the missing tool in the image, add the missing config, or "
            "(for tracked work) add an interim allowlist entry with an issue link.",
            file=sys.stderr,
        )
        return 1

    print("\nGate passed: no non-allowlisted skips.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Fail dogfooding CI when an enabled tool silently skips.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to the lintro JSON report (default: read from stdin).",
    )
    parser.add_argument(
        "--allowlist",
        default="scripts/ci/dogfood-skip-allowlist.yaml",
        help="Path to the skip allowlist YAML file.",
    )
    parser.add_argument(
        "--sibling-report",
        action="append",
        default=[],
        help=(
            "Report from another attempt of the same run (repeatable). A "
            "probe_timeout row is absorbed only when one of these holds a "
            "verdict for the same tool. Read only when the report has such a "
            "row; an unreadable sibling is ignored with a warning."
        ),
    )
    args = parser.parse_args(argv)
    return run(args.report, args.allowlist, args.sibling_report)


if __name__ == "__main__":
    raise SystemExit(main())
