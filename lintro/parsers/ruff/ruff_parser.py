"""Parser for ruff output (lint and format).

This module provides functions to parse both:
- ruff check --output-format json (linting issues)
- ruff format --check (plain text: files needing formatting)

Supports both batch and streaming parsing modes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Iterable

from loguru import logger

from lintro.parsers.base_parser import (
    extract_dict_field,
    extract_int_field,
    extract_str_field,
    safe_parse_items_with_stats,
)
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.streaming import stream_json_array_fallback


def _parse_ruff_item(item: dict[str, object]) -> RuffIssue:
    """Parse a single Ruff issue item into a RuffIssue object.

    Args:
        item: Dictionary containing issue data from Ruff JSON output.

    Returns:
        RuffIssue object with validated filename and message.

    Raises:
        ValueError: If filename or message is missing, blank, or not a string.
    """
    filename = extract_str_field(item, ["filename", "file"])
    message = extract_str_field(item, ["message"])
    if not filename.strip() or not message.strip():
        raise ValueError(
            "Ruff diagnostic requires nonblank filename and message strings",
        )
    loc = extract_dict_field(item, ["location", "start"])
    end_loc = extract_dict_field(item, ["end_location", "end"])

    line = extract_int_field(loc, ["row", "line"], default=0) or 0
    column = extract_int_field(loc, ["column", "col"], default=0) or 0
    end_line = extract_int_field(end_loc, ["row", "line"], default=line) or line
    end_column = extract_int_field(end_loc, ["column", "col"], default=column) or column

    code = extract_str_field(item, ["code", "rule"])
    url_candidate = item.get("url")
    url: str | None = url_candidate if isinstance(url_candidate, str) else None

    fix = extract_dict_field(item, ["fix"])
    fixable: bool = bool(fix)
    fix_applicability_raw = fix.get("applicability") if fix else None
    fix_applicability: str | None = (
        fix_applicability_raw if isinstance(fix_applicability_raw, str) else None
    )

    return RuffIssue(
        file=filename,
        line=line,
        column=column,
        code=code,
        message=message,
        url=url,
        end_line=end_line,
        end_column=end_column,
        fixable=fixable,
        fix_applicability=fix_applicability,
    )


def parse_ruff_output(output: str) -> list[RuffIssue]:
    """Parse Ruff JSON or JSON Lines output into `RuffIssue` objects.

    Supports multiple Ruff schema variants across versions by accepting:
    - JSON array of issue objects
    - JSON Lines (one object per line)

    Field name variations handled:
    - location: "location" or "start" with keys "row"|"line" and
      "column"|"col"
    - end location: "end_location" or "end" with keys "row"|"line" and
      "column"|"col"
    - filename: "filename" (preferred) or "file"

    Args:
        output: Raw output from `ruff check --output-format json`.

    Returns:
        list[RuffIssue]: Parsed issues.
    """
    issues, _failures = parse_ruff_output_with_stats(output)
    return issues


def parse_ruff_output_with_stats(output: str) -> tuple[list[RuffIssue], int]:
    """Parse Ruff output and count malformed or unusable diagnostic records.

    Optional fields, including the null code on a syntax error, retain their
    defaults. Invalid required fields raise inside ``safe_parse_items_with_stats``
    so they count as parse failures rather than intentional filtering. Malformed
    JSON object lines also count as parse failures. An invalid array envelope
    counts as a failure even when JSON Lines fallback recovers diagnostics.

    Args:
        output: JSON array or JSON Lines output from Ruff.

    Returns:
        Parsed issues and the number of rejected diagnostic records.
    """
    if not output.strip() or output.strip() == "[]":
        return [], 0

    # First try JSON array (with possible trailing non-JSON data)
    array_decode_failed = False
    try:
        json_end = output.rfind("]")
        if json_end != -1:
            json_part = output[: json_end + 1]
            ruff_data = json.loads(json_part)
        else:
            ruff_data = json.loads(output)

        if isinstance(ruff_data, list):
            return safe_parse_items_with_stats(ruff_data, _parse_ruff_item, "ruff")
        if not isinstance(ruff_data, dict):
            return [], 1
    except (json.JSONDecodeError, TypeError) as e:
        # Fall back to JSON Lines parsing below
        array_decode_failed = True
        logger.debug(f"Ruff array JSON parsing failed, falling back to JSON Lines: {e}")

    # Fallback: parse JSON Lines (each line is a JSON object)
    items: list[object] = []
    decode_failures = 0
    for line in output.splitlines():
        line_str = line.strip()
        if not line_str or not line_str.startswith("{"):
            continue
        try:
            item = json.loads(line_str)
            if isinstance(item, dict):
                items.append(item)
        except json.JSONDecodeError as e:
            decode_failures += 1
            logger.debug(f"Failed to parse ruff JSON line '{line_str[:50]}': {e}")
            continue

    if (
        array_decode_failed
        and not decode_failures
        and (output.lstrip().startswith("[") or not items)
    ):
        decode_failures = 1

    issues, parse_failures = safe_parse_items_with_stats(
        items,
        _parse_ruff_item,
        "ruff",
    )
    return issues, parse_failures + decode_failures


def parse_ruff_format_check_output(output: str) -> list[str]:
    """Parse the output of `ruff format --check` to get files needing formatting.

    Args:
        output: The raw output from `ruff format --check`

    Returns:
        List of file paths that would be reformatted
    """
    from lintro.parsers.base_parser import strip_ansi_codes

    if not output:
        return []
    files = []
    for raw in output.splitlines():
        # Strip ANSI color codes for stable parsing across environments
        line = strip_ansi_codes(raw).strip()
        # Ruff format --check output: 'Would reformat: path/to/file.py' or
        # 'Would reformat path/to/file.py'
        if line.startswith("Would reformat: "):
            files.append(line[len("Would reformat: ") :])
        elif line.startswith("Would reformat "):
            files.append(line[len("Would reformat ") :])
    # Ruff 0.16's default full report identifies each unformatted file in a
    # diagnostic location instead of a "Would reformat" line.
    files.extend(
        match[1]
        for match in re.finditer(
            r"^unformatted: File would be reformatted\r?\n"
            r"[ \t]*--> (.+):\d+:\d+\r?$",
            strip_ansi_codes(output),
            re.MULTILINE,
        )
    )
    return files


# ---------------------------------------------------------------------------
# Streaming parser variants
# ---------------------------------------------------------------------------


def stream_ruff_output(
    output: str | Iterable[str],
) -> Generator[RuffIssue, None, None]:
    """Stream Ruff JSON output, yielding issues as they are parsed.

    Supports both JSON array and JSON Lines formats. For large outputs,
    this is more memory-efficient than parse_ruff_output() as it yields
    issues incrementally rather than building a full list.

    Rejected records are skipped without a failure count. Execution paths
    that decide success must use parse_ruff_output_with_stats() instead.

    Args:
        output: Raw output from `ruff check --output-format json`, either
            as a complete string or as an iterable of lines.

    Yields:
        RuffIssue: Parsed issues one at a time.

    Examples:
        >>> for issue in stream_ruff_output(ruff_output):
        ...     print(f"{issue.file}:{issue.line}: {issue.message}")
    """
    if isinstance(output, str):
        # Use fallback parser that handles both JSON array and JSON Lines
        yield from stream_json_array_fallback(output, _parse_ruff_item, "ruff")
    else:
        # Iterable of lines - stream JSON Lines directly
        from lintro.parsers.streaming import stream_json_lines

        yield from stream_json_lines(output, _parse_ruff_item, "ruff")


def stream_ruff_format_output(
    output: str | Iterable[str],
) -> Generator[str, None, None]:
    """Stream ruff format --check output, yielding file paths incrementally.

    Args:
        output: Raw output from `ruff format --check`, either as a complete
            string or as an iterable of lines.

    Yields:
        str: File paths that would be reformatted.

    Examples:
        >>> for filepath in stream_ruff_format_output(format_output):
        ...     print(f"Needs formatting: {filepath}")
    """
    from lintro.parsers.base_parser import strip_ansi_codes

    lines: Iterable[str]
    lines = output.splitlines() if isinstance(output, str) else output

    for raw_line in lines:
        line = strip_ansi_codes(raw_line).strip()
        if line.startswith("Would reformat: "):
            yield line[len("Would reformat: ") :]
        elif line.startswith("Would reformat "):
            yield line[len("Would reformat ") :]
