#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Classify lintro osv_scanner JSON output for CI status handling.

Reads lintro JSON output and prints a single status token on stdout:

- ``ok`` — scan completed with zero issues and zero parse failures
- ``vulns`` — one or more vulnerabilities reported
- ``error`` — malformed payload or tool failure indicators

Usage:
    python3 scripts/ci/classify-osv-results.py osv-results.json

Exit codes:
    0 — Classification printed (including ``error`` on unreadable/malformed input)
    1 — Invalid arguments (wrong number of positional arguments)
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any


class OsvResultClass(StrEnum):
    """Classification tokens emitted for security-comment.sh."""

    OK = "ok"
    VULNS = "vulns"
    ERROR = "error"


def _classify_single_osv_result(*, result: dict[str, Any]) -> OsvResultClass:
    """Return the audit class for one osv_scanner result entry."""
    if "issues_count" not in result or "parse_failures_count" not in result:
        return OsvResultClass.ERROR

    issues_count = result["issues_count"]
    parse_failures = result["parse_failures_count"]
    if type(issues_count) is not int or type(parse_failures) is not int:
        return OsvResultClass.ERROR
    if issues_count < 0 or parse_failures < 0:
        return OsvResultClass.ERROR
    if parse_failures > 0:
        return OsvResultClass.ERROR
    if issues_count > 0:
        return OsvResultClass.VULNS
    if result.get("success") is not True:
        return OsvResultClass.ERROR
    return OsvResultClass.OK


def classify_osv_results(*, payload: dict[str, Any]) -> OsvResultClass:
    """Return the audit class for a parsed lintro JSON payload."""
    results = payload.get("results", [])
    if not isinstance(results, list):
        return OsvResultClass.ERROR

    osv_results = [
        entry
        for entry in results
        if isinstance(entry, dict) and entry.get("tool") == "osv_scanner"
    ]
    if not osv_results:
        return OsvResultClass.ERROR

    classifications = [
        _classify_single_osv_result(result=entry) for entry in osv_results
    ]
    if OsvResultClass.ERROR in classifications:
        return OsvResultClass.ERROR
    if OsvResultClass.VULNS in classifications:
        return OsvResultClass.VULNS
    return OsvResultClass.OK


def main() -> int:
    """Classify osv_scanner results from a lintro JSON file."""
    if len(sys.argv) != 2:
        print(
            f"Usage: {Path(sys.argv[0]).name} osv-results.json",
            file=sys.stderr,
        )
        return 1

    results_path = Path(sys.argv[1])
    try:
        payload = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Failed to parse {results_path}: {exc}", file=sys.stderr)
        print(OsvResultClass.ERROR.value)
        return 0

    try:
        status = classify_osv_results(payload=payload)
    except Exception as exc:
        print(f"Unexpected {results_path.name} shape: {exc}", file=sys.stderr)
        print(OsvResultClass.ERROR.value)
        return 0

    print(status.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
