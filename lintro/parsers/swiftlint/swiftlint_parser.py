"""Parser for SwiftLint JSON output.

SwiftLint's ``--reporter json`` emits a single JSON array where each element
describes one violation. This module converts that payload into a list of
``SwiftlintIssue`` objects, defensively handling empty, null, or malformed
input by returning an empty list rather than raising.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.swiftlint.swiftlint_issue import SwiftlintIssue


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int with a fallback.

    Args:
        value: Value to convert.
        default: Default value if conversion fails.

    Returns:
        Integer value or the provided default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_swiftlint_output(output: str | None) -> list[SwiftlintIssue]:
    """Parse SwiftLint JSON output into a list of ``SwiftlintIssue`` objects.

    SwiftLint outputs JSON in the following format when using
    ``--reporter json``::

        [
          {
            "file": "/abs/path/Sample.swift",
            "line": 4,
            "character": 9,
            "severity": "Error",
            "type": "Identifier Name",
            "rule_id": "identifier_name",
            "reason": "Variable name 'x' should be between 3 and 40 characters long"
          }
        ]

    Args:
        output: The raw JSON output from SwiftLint, or ``None``.

    Returns:
        List of ``SwiftlintIssue`` objects. Returns an empty list when there
        are no issues or the output cannot be decoded.
    """
    issues: list[SwiftlintIssue] = []

    if output is None or not output.strip():
        return issues

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        logger.debug(f"Failed to parse swiftlint output as JSON: {exc}")
        return issues

    if not isinstance(parsed, list):
        return issues

    for item in parsed:
        if not isinstance(item, dict):
            continue

        file_path = str(item.get("file", ""))
        line = _safe_int(item.get("line", 0))
        # SwiftLint reports the column under the "character" key.
        column = _safe_int(item.get("character", 0))
        severity = item.get("severity")
        rule_type = item.get("type")
        code = str(item.get("rule_id", ""))
        message = str(item.get("reason", ""))

        issues.append(
            SwiftlintIssue(
                file=file_path,
                line=line,
                column=column,
                code=code,
                message=message,
                level=str(severity) if severity is not None else None,
                rule_type=str(rule_type) if rule_type is not None else None,
            ),
        )

    return issues
