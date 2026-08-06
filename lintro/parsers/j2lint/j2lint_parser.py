"""Parser for j2lint JSON output.

j2lint emits a single JSON object with two arrays, ``ERRORS`` and
``WARNINGS``. Each entry has the shape::

    {
        "id": "S3",
        "message": "Bad Indentation, expected 4, got 1",
        "filename": "template.j2",
        "line_number": 3,
        "line": "{%- for item in items %}",
        "severity": "HIGH"
    }

Entries under ``ERRORS`` are mapped to level ``error`` and entries under
``WARNINGS`` (populated when a rule is demoted via ``--warn``) to level
``warning``. This module converts that JSON into structured
``J2lintIssue`` objects so Lintro can render uniform tables.
"""

from __future__ import annotations

import json
from typing import Any

from lintro.parsers.j2lint.j2lint_issue import J2lintIssue


def _extract_json_object(output: str) -> dict[str, Any] | None:
    """Extract the JSON object from raw j2lint stdout.

    Args:
        output: Raw stdout text emitted by ``j2lint --json``.

    Returns:
        Parsed JSON object, or None when no valid object can be located.
    """
    text = output.strip()
    if not text:
        return None

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _build_issue(entry: dict[str, Any], *, level: str) -> J2lintIssue | None:
    """Build a single ``J2lintIssue`` from a raw JSON entry.

    Args:
        entry: A single object from the ``ERRORS`` or ``WARNINGS`` array.
        level: Bucket-derived severity ("error" or "warning").

    Returns:
        A populated ``J2lintIssue``, or None when the entry is malformed.
    """
    if not isinstance(entry, dict):
        return None

    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename:
        return None

    line_raw = entry.get("line_number")
    line_number = (
        line_raw
        if isinstance(line_raw, int)
        and not isinstance(
            line_raw,
            bool,
        )
        else 0
    )

    message = entry.get("message")
    code = entry.get("id")
    native_severity = entry.get("severity")
    source_line = entry.get("line")

    return J2lintIssue(
        file=filename,
        line=line_number,
        column=0,
        level=level,
        code=str(code) if code else "",
        native_severity=str(native_severity) if native_severity else "",
        source_line=str(source_line) if source_line else "",
        message=str(message) if message else "",
    )


def parse_j2lint_output(output: str | None) -> list[J2lintIssue]:
    """Parse raw j2lint JSON output into structured issues.

    Args:
        output: Raw stdout from ``j2lint --json``. May be None or empty.

    Returns:
        list[J2lintIssue]: Parsed issues. Returns an empty list for empty,
            missing, or unparseable output.
    """
    if not output:
        return []

    data = _extract_json_object(output)
    if data is None:
        return []

    issues: list[J2lintIssue] = []
    for bucket, level in (("ERRORS", "error"), ("WARNINGS", "warning")):
        entries = data.get(bucket, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            issue = _build_issue(entry, level=level)
            if issue is not None:
                issues.append(issue)

    return issues
