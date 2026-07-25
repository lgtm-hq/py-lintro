#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Classify a lintro JSON report as a tool-execution-timeout infra flake.

A tool that exceeds its execution timeout (``mypy execution timed out
(120.0s limit exceeded)``) makes lintro exit ``1`` with ``status=failed`` —
structurally identical to a real lint verdict. The code-quality gate therefore
cannot tell a perf flake from genuine findings using job outputs alone
(issue #1653).

This script supplies the missing evidence. It reads the structured report
produced by ``lintro chk --output-format json --output <file>`` (the same
report the no-silent-skip gate already generates from the same image and the
same tree) and answers one question: *did this run fail only because a tool
timed out, with zero lint findings anywhere?*

Classification is deliberately conservative and fails closed. It reports
``timeout-flake=true`` only when **all** of the following hold:

- the report parses and carries a ``summary.total_issues`` of ``0``;
- at least one non-skipped tool timed out — its ``output`` matches a timeout
  signature (``execution timed out`` / ``limit exceeded``) or it recorded a
  timeout exit code (124, 143);
- every timed-out tool contributed zero issues, so a timeout can never mask a
  finding it did report;
- every other non-skipped tool succeeded with zero issues.

Anything else — a missing summary, a malformed document, a second tool that
failed for a non-timeout reason, or any issue at all — reports
``timeout-flake=false``. Absence of evidence is never treated as evidence of a
flake.

Usage:
    python3 scripts/ci/classify-lint-timeout.py --report results.json

    # or read the report from stdin
    lintro chk --output-format json . | \
        python3 scripts/ci/classify-lint-timeout.py --report -

Outputs (stdout, and appended to ``GITHUB_OUTPUT`` when set):
    timeout-flake=true|false
    timed-out-tools=<comma-separated tool names>

Exit codes:
    0 — classification completed (read the ``timeout-flake`` output)
    2 — usage error (missing/unreadable report)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Signatures emitted by lintro's own timeout handling
# (``lintro/tools/core/timeout_utils.py``). Matched against a tool's captured
# ``output`` only — never against a whole job log — so an unrelated substring
# elsewhere in CI can never green the required check.
TIMEOUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"execution timed out", re.IGNORECASE),
    re.compile(r"\blimit exceeded\b", re.IGNORECASE),
)

# Exit codes attributable to a killed/timed-out tool process: 124 is GNU
# ``timeout``'s deadline verdict, 143 is SIGTERM (128 + 15). lintro itself
# only ever uses 1 for a lint violation, so neither can be a lint verdict.
TIMEOUT_EXIT_CODES: frozenset[int] = frozenset({124, 143})

# Tool names are echoed into GITHUB_OUTPUT, a line-oriented key=value file.
# Restrict them to a conservative charset so a crafted report cannot forge an
# extra record (the same injection guard evaluate-code-quality-gate.sh applies).
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class Classification:
    """The verdict for one lintro JSON report.

    Attributes:
        timeout_flake: True when the run failed only because one or more tools
            timed out and no tool reported any issue.
        timed_out_tools: Names of the tools that showed a timeout signature.
        reason: Human-readable explanation of the verdict, for CI logs.
    """

    timeout_flake: bool
    timed_out_tools: tuple[str, ...] = field(default=())
    reason: str = ""


def _result_issue_count(result: dict[str, Any]) -> int:
    """Return the issue count a tool result reports.

    Uses the larger of ``issues_count`` and the length of the ``issues``
    array so a report that carries only one of the two still fails closed.

    Args:
        result: One per-tool object from the lintro report.

    Returns:
        The number of issues attributed to the tool.
    """
    raw_count = result.get("issues_count", 0)
    count = raw_count if isinstance(raw_count, int) and raw_count > 0 else 0
    issues = result.get("issues")
    if isinstance(issues, list):
        count = max(count, len(issues))
    return count


def _looks_like_timeout(result: dict[str, Any]) -> bool:
    """Report whether a tool result carries a timeout signature.

    Args:
        result: One per-tool object from the lintro report.

    Returns:
        True when the tool's captured output matches a timeout message or it
        recorded a timeout exit code.
    """
    output = result.get("output")
    if isinstance(output, str) and any(
        pattern.search(output) for pattern in TIMEOUT_PATTERNS
    ):
        return True
    exit_code = result.get("exit_code")
    return isinstance(exit_code, int) and exit_code in TIMEOUT_EXIT_CODES


def _tool_name(result: dict[str, Any]) -> str:
    """Return a safe display name for a tool result.

    Args:
        result: One per-tool object from the lintro report.

    Returns:
        The tool name when it matches the safe charset, else ``unknown``.
    """
    name = str(result.get("tool") or "").strip()
    return name if _SAFE_TOOL_NAME.match(name) else "unknown"


def classify(payload: Any) -> Classification:
    """Classify a parsed lintro JSON report.

    Args:
        payload: The parsed ``lintro chk --output-format json`` document.

    Returns:
        The :class:`Classification` verdict. Every failure to prove the flake
        — malformed payload, missing summary, any issue, any non-timeout tool
        failure — yields ``timeout_flake=False``.
    """
    if not isinstance(payload, dict):
        return Classification(False, reason="report is not a JSON object")

    results = payload.get("results")
    if not isinstance(results, list):
        return Classification(False, reason="report has no 'results' array")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return Classification(False, reason="report has no 'summary' object")
    total_issues = summary.get("total_issues")
    if not isinstance(total_issues, int) or total_issues != 0:
        return Classification(
            False,
            reason=f"report has findings (total_issues={total_issues!r})",
        )

    timed_out: list[str] = []
    for entry in results:
        if not isinstance(entry, dict) or entry.get("skipped"):
            continue
        name = _tool_name(entry)
        issue_count = _result_issue_count(entry)
        if _looks_like_timeout(entry):
            if issue_count:
                return Classification(
                    False,
                    reason=f"{name} timed out but reported {issue_count} issue(s)",
                )
            timed_out.append(name)
            continue
        if entry.get("success") is not True:
            return Classification(
                False,
                reason=f"{name} failed for a non-timeout reason",
            )
        if issue_count:
            return Classification(
                False,
                reason=f"{name} reported {issue_count} issue(s)",
            )

    if not timed_out:
        return Classification(False, reason="no tool reported an execution timeout")

    return Classification(
        True,
        timed_out_tools=tuple(timed_out),
        reason=f"only execution timeouts, zero findings: {', '.join(timed_out)}",
    )


def _read_payload(report: str) -> Any:
    """Read and parse the report from a path or stdin.

    Args:
        report: Path to the JSON report, or ``-`` for stdin.

    Propagates ``OSError`` when the report path cannot be read; the caller
    turns that into a usage error rather than a silent false verdict.

    Returns:
        The parsed document, or ``None`` when it is not valid JSON.
    """
    text = sys.stdin.read() if report == "-" else Path(report).read_text("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _emit(classification: Classification) -> None:
    """Write the classification to stdout and ``GITHUB_OUTPUT``.

    Args:
        classification: The verdict to publish.
    """
    lines = (
        f"timeout-flake={'true' if classification.timeout_flake else 'false'}",
        f"timed-out-tools={','.join(classification.timed_out_tools)}",
    )
    for line in lines:
        print(line)
    print(f"[INFO] {classification.reason}", file=sys.stderr)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Run the classifier.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 when classification completed, 2 on usage error.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Classify a lintro JSON report as a tool-execution-timeout "
            "infra flake (#1653)."
        ),
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to the lintro JSON report, or '-' to read stdin.",
    )
    args = parser.parse_args(argv)

    try:
        payload = _read_payload(args.report)
    except OSError as exc:
        print(f"[ERROR] cannot read report {args.report}: {exc}", file=sys.stderr)
        return 2

    _emit(classify(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
