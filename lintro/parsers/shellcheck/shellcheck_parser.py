"""Parser for shellcheck JSON output.

This module provides parsing functionality for ShellCheck's json1 output format.
ShellCheck is a static analysis tool for shell scripts that identifies bugs,
syntax issues, and suggests improvements.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import extract_optional_str
from lintro.parsers.shellcheck.shellcheck_issue import ShellcheckIssue


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int with fallback.

    Args:
        value: Value to convert.
        default: Default value if conversion fails.

    Returns:
        Integer value or default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_shellcheck_output(output: str | None) -> list[ShellcheckIssue]:
    """Parse shellcheck json1 output into a list of ShellcheckIssue objects.

    ShellCheck wraps its comments in an object when using --format=json1:
    {"comments": [
      {
        "file": "script.sh",
        "line": 10,
        "endLine": 10,
        "column": 5,
        "endColumn": 10,
        "level": "warning",
        "code": 2086,
        "message": "Double quote to prevent globbing and word splitting."
      }
    ]}

    Legacy top-level arrays are also supported.

    Args:
        output: The raw JSON output from shellcheck, or None.

    Returns:
        List of ShellcheckIssue objects.
    """
    issues, _failures = parse_shellcheck_output_with_stats(output)
    return issues


def parse_shellcheck_output_with_stats(
    output: str | None,
) -> tuple[list[ShellcheckIssue], int]:
    """Parse ShellCheck findings and count malformed report containers.

    json1 objects require a list-valued ``comments`` field. Legacy top-level
    lists and absent or blank output remain supported. Invalid JSON or an
    invalid report container counts as one failure, with no findings.

    Args:
        output: The raw JSON output from shellcheck, or None.

    Returns:
        Parsed findings and the number of malformed reports.
    """
    issues: list[ShellcheckIssue] = []

    # Handle None or empty output
    if output is None or not output.strip():
        return issues, 0

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse shellcheck output as JSON: {e}")
        return issues, 1

    # Handle json1 format: {"comments": [...]} or plain JSON format: [...]
    # Note: data may contain non-dict items, filtered by isinstance check below
    if isinstance(parsed, dict):
        data = parsed.get("comments")
    elif isinstance(parsed, list):
        data = parsed
    else:
        return issues, 1

    if not isinstance(data, list):
        logger.debug("Shellcheck json1 comments must be a list")
        return issues, 1

    for item in data:
        if not isinstance(item, dict):
            continue

        # Extract required fields with defaults (using safe conversion)
        file_path = extract_optional_str(item, "file")
        line: int = _safe_int(item.get("line", 0))
        column: int = _safe_int(item.get("column", 0))
        end_line: int = _safe_int(item.get("endLine", 0))
        end_column: int = _safe_int(item.get("endColumn", 0))
        level = extract_optional_str(item, "level", "error")
        code: object = item.get("code", 0)
        message = extract_optional_str(item, "message")

        # Format code as SC#### string (handle both int and numeric string codes)
        if (isinstance(code, int) and not isinstance(code, bool)) or (
            isinstance(code, str) and code.isdigit()
        ):
            code_str = f"SC{code}"
        else:
            code_str = extract_optional_str(item, "code")

        issues.append(
            ShellcheckIssue(
                file=file_path,
                line=line,
                column=column,
                end_line=end_line,
                end_column=end_column,
                level=level,
                code=code_str,
                message=message,
            ),
        )

    return issues, 0
