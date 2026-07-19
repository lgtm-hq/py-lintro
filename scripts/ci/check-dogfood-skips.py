#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Fail dogfooding CI when an enabled tool silently skips.

A SKIP is indistinguishable from a PASS in job status, so a wrapped tool can
be dead for weeks (missing binary) or forever (missing config) without anyone
noticing. This gate parses lintro's structured JSON output
(``lintro chk --output-format json``) and exits non-zero when any enabled tool
reports ``skipped: true`` for a reason that is not covered by the committed
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
- ``other`` — an unrecognised skip reason; treated like ``no_config`` for
  allowlisting purposes (must be explicitly listed to be tolerated).

Usage:
    python3 scripts/ci/check-dogfood-skips.py \
        --report results.json \
        --allowlist scripts/ci/dogfood-skip-allowlist.yaml

    # or read the JSON report from stdin
    lintro chk --output-format json . | \
        python3 scripts/ci/check-dogfood-skips.py \
            --allowlist scripts/ci/dogfood-skip-allowlist.yaml

Exit codes:
    0 — no non-allowlisted skips (gate passes)
    1 — one or more non-allowlisted skips (gate fails)
    2 — usage / configuration error (bad args, unreadable report or allowlist,
        or an allowlist that violates the schema)
"""

from __future__ import annotations

import argparse
import json
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

    Matching is case-insensitive and substring-based, checked in priority
    order so the most dangerous class (a missing binary) wins.

    Args:
        reason: The ``skip_reason`` string from a lintro tool result.

    Returns:
        The classified :class:`SkipClass`. An empty/None reason is treated as
        ``other`` (an unexplained skip is never silently tolerated).
    """
    text = (reason or "").lower()
    if not text:
        return SkipClass.OTHER

    binary_markers = (
        "version check",
        "no such file or directory",
        "command not found",
        "not found in path",
        "is not installed",
        "not installed",
        "executable not found",
    )
    if any(marker in text for marker in binary_markers):
        return SkipClass.BINARY_MISSING

    opt_in_markers = (
        "disabled by default",
        "opt-in",
        "opt in",
        "disabled (opt-in",
    )
    if any(marker in text for marker in opt_in_markers):
        return SkipClass.OPT_IN_DISABLED

    config_markers = (
        "no config",
        "no configuration",
        "configuration found",
        "configuration provided",
        "config found",
        ".vale.ini",
    )
    if any(marker in text for marker in config_markers):
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


def evaluate_skips(payload: Any, allowlist: Allowlist) -> list[SkipFinding]:
    """Evaluate every skipped tool in a lintro report against the allowlist.

    Args:
        payload: Parsed lintro JSON report.
        allowlist: The parsed allowlist.

    Returns:
        One :class:`SkipFinding` per skipped tool, in report order.
    """
    findings: list[SkipFinding] = []
    for result in _extract_results(payload):
        if not result.get("skipped"):
            continue
        tool = str(result.get("tool") or "unknown")
        reason = result.get("skip_reason")
        reason_str = reason if isinstance(reason, str) else ""
        skip_class = classify_skip_reason(reason_str)
        entry = allowlist.get(tool)

        if entry is None:
            findings.append(
                SkipFinding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    allowed=False,
                    message=(
                        f"{tool}: skipped ({skip_class.value}) with no allowlist "
                        f"entry — reason: {reason_str or '<none>'}"
                    ),
                ),
            )
            continue

        if entry.reason_class != skip_class:
            findings.append(
                SkipFinding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    allowed=False,
                    message=(
                        f"{tool}: skipped as {skip_class.value} but allowlisted for "
                        f"{entry.reason_class.value} — re-triage the skip reason: "
                        f"{reason_str or '<none>'}"
                    ),
                ),
            )
            continue

        # Allowlisted skip. Interim binary-missing tolerations are surfaced as
        # loud warnings so an image bug can never be masked silently forever.
        issue_ref = f" (see #{entry.issue})" if entry.issue is not None else ""
        if entry.interim:
            findings.append(
                SkipFinding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    allowed=True,
                    is_warning=True,
                    message=(
                        f"{tool}: interim-allowlisted skip ({skip_class.value})"
                        f"{issue_ref} — {entry.rationale}"
                    ),
                ),
            )
        else:
            findings.append(
                SkipFinding(
                    tool=tool,
                    reason=reason_str,
                    skip_class=skip_class,
                    allowed=True,
                    message=(
                        f"{tool}: allowlisted skip ({skip_class.value}) — "
                        f"{entry.rationale}"
                    ),
                ),
            )
    return findings


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


def run(report_arg: str | None, allowlist_arg: str) -> int:
    """Run the gate and return the process exit code.

    Args:
        report_arg: Path to the lintro JSON report, or ``None``/``-`` for stdin.
        allowlist_arg: Path to the allowlist YAML file.

    Returns:
        Exit code: 0 (pass), 1 (violations), or 2 (usage/config error).
    """
    try:
        payload = _read_report(report_arg)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to read lintro report: {exc}", file=sys.stderr)
        return 2

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
        findings = evaluate_skips(payload, allowlist)
    except ValueError as exc:
        print(f"error: malformed lintro report: {exc}", file=sys.stderr)
        return 2

    _print_report(findings)

    violations = [f for f in findings if not f.allowed]
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
    args = parser.parse_args(argv)
    return run(args.report, args.allowlist)


if __name__ == "__main__":
    raise SystemExit(main())
