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

    ShellCheck outputs JSON in the following format when using --format=json1:
    [
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
    ]

    Args:
        output: The raw JSON output from shellcheck, or None.

    Returns:
        List of ShellcheckIssue objects.
    """
    issues: list[ShellcheckIssue] = []

    # Handle None or empty output
    if output is None or not output.strip():
        return issues

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        # If JSON parsing fails, return empty list
        logger.debug(f"Failed to parse shellcheck output as JSON: {e}")
        return issues

    # Handle json1 format: {"comments": [...]} or plain JSON format: [...]
    # Note: data may contain non-dict items, filtered by isinstance check below
    if isinstance(parsed, dict) and "comments" in parsed:
        data: list[Any] = parsed["comments"]
    elif isinstance(parsed, list):
        data = parsed
    else:
        return issues

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

    return issues
