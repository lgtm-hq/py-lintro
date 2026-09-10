"""Parser for buf output.

This module parses two distinct buf output shapes into ``BufIssue`` objects:

- ``buf lint --error-format json`` emits newline-delimited JSON objects (one
  violation per line) with ``path``, ``start_line``, ``start_column``,
  ``end_line``, ``end_column``, ``type`` and ``message`` keys. Compile/parse
  errors use the ``COMPILE`` rule id but share the same JSON shape.
- ``buf format -d`` emits a unified diff to stdout. Each formatted-away file is
  identified by its ``+++`` header line; ``parse_buf_format_output`` turns those
  into ``FORMAT`` issues so formatting problems surface alongside lint findings.
"""

from __future__ import annotations

import json
import re

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.buf.buf_issue import BufIssue

# Unified-diff target header, e.g. "+++ path/to/file.proto\t2026-07-07 ...".
# The path runs up to the first tab (diff appends a timestamp) or end of line.
_DIFF_TARGET_PATTERN: re.Pattern[str] = re.compile(r"^\+\+\+\s+(?P<path>[^\t]+)")


def parse_buf_output(output: str | None) -> list[BufIssue]:
    """Parse ``buf lint --error-format json`` output into BufIssue objects.

    buf writes one JSON object per line. Lines that are blank or not valid
    JSON objects (e.g. stray log lines) are skipped rather than raising, so a
    single malformed line never discards the whole report.

    Args:
        output: The raw stdout from ``buf lint --error-format json``, or None.

    Returns:
        List of BufIssue objects parsed from the output.
    """
    issues: list[BufIssue] = []

    if not output or not output.strip():
        return issues

    output = strip_ansi_codes(output)

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(record, dict):
            continue

        path = record.get("path")
        if not path:
            continue

        code = str(record.get("type") or "")
        issues.append(
            BufIssue(
                file=str(path),
                line=_as_int(record.get("start_line")),
                column=_as_int(record.get("start_column")),
                end_line=_as_int(record.get("end_line")),
                end_column=_as_int(record.get("end_column")),
                level="error",
                code=code,
                message=str(record.get("message") or ""),
            ),
        )

    return issues


def parse_buf_format_output(output: str | None) -> list[BufIssue]:
    """Parse ``buf format -d`` diff output into FORMAT BufIssue objects.

    Each file that differs from its formatted form appears as a ``+++`` target
    header in the unified diff. One ``FORMAT`` issue is emitted per such file.

    Args:
        output: The raw stdout from ``buf format -d``, or None.

    Returns:
        List of BufIssue objects (one per unformatted file), deduplicated by
        file path while preserving first-seen order.
    """
    issues: list[BufIssue] = []

    if not output or not output.strip():
        return issues

    output = strip_ansi_codes(output)

    seen: set[str] = set()
    for line in output.splitlines():
        match = _DIFF_TARGET_PATTERN.match(line)
        if not match:
            continue
        path = match.group("path").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        issues.append(
            BufIssue(
                file=path,
                line=0,
                column=0,
                level="error",
                code="FORMAT",
                message="File is not formatted (run buf format to fix)",
            ),
        )

    return issues


def _as_int(value: object) -> int:
    """Coerce a JSON value to a non-negative int, defaulting to 0.

    Args:
        value: The raw value from the parsed JSON record.

    Returns:
        The integer value, or 0 when missing or non-numeric.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
