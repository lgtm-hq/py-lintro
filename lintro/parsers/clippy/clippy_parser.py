"""Parser for Clippy cargo diagnostic JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import extract_optional_str, strip_ansi_codes
from lintro.parsers.clippy.clippy_issue import ClippyIssue


def _parse_issue(item: dict[str, Any]) -> ClippyIssue | None:
    """Convert a Clippy diagnostic JSON object into a ``ClippyIssue``.

    Args:
        item: A single diagnostic payload returned by cargo clippy in JSON form.

    Returns:
        A populated ``ClippyIssue`` instance or ``None`` when the payload cannot
        be parsed.
    """
    try:
        # Clippy outputs cargo diagnostic format:
        # {
        #   "reason": "compiler-message",
        #   "message": {
        #     "code": {"code": "clippy::needless_return"},
        #     "level": "warning",
        #     "message": "unneeded `return` statement",
        #     "spans": [{
        #       "file_name": "src/lib.rs",
        #       "line_start": 42,
        #       "line_end": 42,
        #       "column_start": 5,
        #       "column_end": 15
        #     }]
        #   }
        # }
        if item.get("reason") != "compiler-message":
            return None

        message = item.get("message", {})
        if not isinstance(message, dict):
            return None

        # Extract code
        code_obj = message.get("code")
        code: str | None = None
        if isinstance(code_obj, dict):
            code = extract_optional_str(code_obj, "code")
        elif isinstance(code_obj, str):
            code = code_obj

        # Only process Clippy lints (skip regular compiler errors)
        if not code or not code.startswith("clippy::"):
            return None

        # Extract message text
        message_text = extract_optional_str(message, "message").strip()
        if not message_text:
            return None

        # Extract level
        level = message.get("level")
        if level not in ("warning", "error", "note", "help"):
            return None

        # Extract spans (location information)
        spans = message.get("spans", [])
        if not spans or not isinstance(spans, list):
            return None

        # Use the primary span (first one)
        primary_span = spans[0]
        if not isinstance(primary_span, dict):
            return None

        file_name = primary_span.get("file_name")
        if not file_name or not isinstance(file_name, str):
            return None

        line_start = primary_span.get("line_start")
        line_end = primary_span.get("line_end")
        column_start = primary_span.get("column_start")
        column_end = primary_span.get("column_end")

        line = int(line_start) if line_start is not None else 0
        column = int(column_start) if column_start is not None else 0
        end_line = int(line_end) if line_end is not None else line
        end_column = int(column_end) if column_end is not None else column

        return ClippyIssue(
            file=file_name,
            line=line,
            column=column,
            code=code,
            message=message_text,
            level=str(level) if level else None,
            end_line=end_line if end_line != line else None,
            end_column=end_column if end_column != column else None,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse clippy diagnostic: {e}")
        return None


def parse_clippy_output(output: str) -> list[ClippyIssue]:
    """Parse Clippy JSON Lines output into ``ClippyIssue`` objects.

    Args:
        output: Raw stdout emitted by cargo clippy using ``--message-format=json``.

    Returns:
        A list of ``ClippyIssue`` instances parsed from the output. Returns an
        empty list when no issues are present or the output cannot be decoded.
    """
    if not output or not output.strip():
        return []

    # Strip ANSI codes for consistent parsing across environments
    output = strip_ansi_codes(output)

    issues: list[ClippyIssue] = []

    # Clippy outputs JSON Lines (one object per line)
    for line in output.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                continue
            parsed = _parse_issue(data)
            if parsed is not None:
                issues.append(parsed)
        except json.JSONDecodeError:
            continue

    return issues
