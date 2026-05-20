"""Base parser utilities for all linting tool parsers.

This module provides common parsing utilities that are shared across multiple
tool parsers to reduce code duplication and ensure consistent behavior.

The utilities include:
- Field extraction with fallback candidates
- ANSI code stripping for terminal output
- Type validation with logging
- Multi-line message collection
- Safe item parsing with error handling
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

IssueT = TypeVar("IssueT", bound="BaseIssue")

# Pre-compiled regex for ANSI escape sequences
_ANSI_ESCAPE_PATTERN: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")


def extract_int_field(
    data: dict[str, object],
    candidates: list[str],
    default: int | None = None,
) -> int | None:
    """Extract an integer field from a dictionary using fallback candidates.

    Tries each candidate key in order until an integer value is found.
    This handles tool output format variations where the same field
    may have different names across versions (e.g., "row" vs "line").

    Args:
        data: Dictionary to extract the field from.
        candidates: List of possible key names to try in order.
        default: Default value if no candidate key has an integer value.

    Returns:
        The integer value from the first matching candidate, or the default.

    Examples:
        >>> data = {"row": 10, "col": 5}
        >>> extract_int_field(data, ["line", "row"])
        10
        >>> extract_int_field(data, ["missing"], default=0)
        0
    """
    for key in candidates:
        val = data.get(key)
        # Check for int but exclude bool (bool is a subclass of int in Python)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
    return default


def extract_str_field(
    data: dict[str, object],
    candidates: list[str],
    default: str = "",
) -> str:
    """Extract a string field from a dictionary using fallback candidates.

    Tries each candidate key in order until a string value is found.
    This handles tool output format variations where the same field
    may have different names across versions (e.g., "filename" vs "file").

    Args:
        data: Dictionary to extract the field from.
        candidates: List of possible key names to try in order.
        default: Default value if no candidate key has a string value.

    Returns:
        The string value from the first matching candidate, or the default.

    Examples:
        >>> data = {"filename": "test.py", "path": "/src/test.py"}
        >>> extract_str_field(data, ["file", "filename"])
        'test.py'
        >>> extract_str_field(data, ["missing"], default="unknown")
        'unknown'
    """
    for key in candidates:
        val = data.get(key)
        if isinstance(val, str):
            return val
    return default


def extract_dict_field(
    data: dict[str, object],
    candidates: list[str],
    default: dict[str, object] | None = None,
) -> dict[str, object]:
    """Extract a dictionary field from a dictionary using fallback candidates.

    Tries each candidate key in order until a dictionary value is found.
    This handles nested structures like location objects that may have
    different names across tool versions.

    Args:
        data: Dictionary to extract the field from.
        candidates: List of possible key names to try in order.
        default: Default value if no candidate key has a dict value.

    Returns:
        The dictionary value from the first matching candidate, or the default.

    Examples:
        >>> data = {"location": {"line": 1}, "start": {"row": 2}}
        >>> extract_dict_field(data, ["location", "start"])
        {'line': 1}
    """
    if default is None:
        default = {}
    for key in candidates:
        val = data.get(key)
        if isinstance(val, dict):
            return val
    return default


def is_empty_output(output: str | None) -> bool:
    r"""Check if output is None, empty, or whitespace only.

    A convenience function for consistent empty-checking across parsers.

    Args:
        output: Output string to check, may be None.

    Returns:
        True if output is None, empty string, or contains only whitespace.

    Examples:
        >>> is_empty_output(None)
        True
        >>> is_empty_output("")
        True
        >>> is_empty_output("   \n\t  ")
        True
        >>> is_empty_output("content")
        False
    """
    return output is None or not output.strip()


def strip_ansi_codes(text: str) -> str:
    r"""Strip ANSI escape sequences from text.

    Removes terminal color codes and other ANSI escape sequences
    for stable parsing across different environments (CI vs local).

    Args:
        text: Text potentially containing ANSI escape sequences.

    Returns:
        Text with all ANSI escape sequences removed.

    Examples:
        >>> strip_ansi_codes("\\x1b[31mError\\x1b[0m: message")
        'Error: message'
        >>> strip_ansi_codes("plain text")
        'plain text'
    """
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def validate_str_field(
    value: object,
    field_name: str,
    default: str = "",
    log_warning: bool = False,
) -> str:
    """Validate and extract a string field with optional warning logging.

    Args:
        value: The value to validate.
        field_name: Name of the field for logging purposes.
        default: Default value if validation fails.
        log_warning: Whether to log a warning on type mismatch.

    Returns:
        The value as a string, or the default if not a string.

    Examples:
        >>> validate_str_field("test", "filename")
        'test'
        >>> validate_str_field(123, "filename", default="unknown")
        'unknown'
    """
    if isinstance(value, str):
        return value
    if log_warning and value is not None:
        logger.warning(f"Expected string for {field_name}, got {type(value).__name__}")
    return default


def validate_int_field(
    value: object,
    field_name: str,
    default: int = 0,
    log_warning: bool = False,
) -> int:
    """Validate and extract an integer field with optional warning logging.

    Args:
        value: The value to validate.
        field_name: Name of the field for logging purposes.
        default: Default value if validation fails.
        log_warning: Whether to log a warning on type mismatch.

    Returns:
        The value as an integer, or the default if not an integer.

    Examples:
        >>> validate_int_field(42, "line_number")
        42
        >>> validate_int_field("not_int", "line_number", default=0)
        0
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if log_warning and value is not None:
        logger.warning(f"Expected integer for {field_name}, got {type(value).__name__}")
    return default


def collect_continuation_lines(
    lines: list[str],
    start_idx: int,
    is_continuation: Callable[[str], bool],
) -> tuple[str, int]:
    """Collect continuation lines that belong to a multi-line message.

    Some tools output messages that span multiple lines with indentation
    or special prefixes. This function collects those lines into a single
    message string.

    Args:
        lines: List of all output lines.
        start_idx: Index of the first continuation line to check.
        is_continuation: Predicate function that returns True if a line
            is a continuation of the message.

    Returns:
        Tuple of (collected message parts joined by space, next index to process).

    Examples:
        >>> lines = ["main message", "    continued", "    more", "next item"]
        >>> collect_continuation_lines(lines, 1, lambda l: l.startswith("    "))
        ('continued more', 3)
    """
    message_parts: list[str] = []
    idx = start_idx

    while idx < len(lines):
        line = lines[idx]
        if not is_continuation(line):
            break
        # Strip common prefixes used in continuation lines
        cleaned = line.strip().lstrip(": ")
        if cleaned:
            message_parts.append(cleaned)
        idx += 1

    return " ".join(message_parts), idx


def safe_parse_items(
    items: list[object],
    parse_func: Callable[[dict[str, object]], IssueT | None],
    tool_name: str = "tool",
) -> list[IssueT]:
    """Safely parse a list of items with error handling.

    Iterates through items, applying the parse function to each dictionary
    and collecting successful results. Non-dict items and parse failures
    are logged and skipped.

    Args:
        items: List of items to parse (expected to be dictionaries).
        parse_func: Function that parses a single item dict into an issue object.
            Should return None if the item cannot be parsed.
        tool_name: Name of the tool for log messages.

    Returns:
        List of successfully parsed issue objects.

    Examples:
        >>> def parse_item(item: dict) -> MyIssue | None:
        ...     return MyIssue(file=item.get("file", ""))
        >>> items = [{"file": "a.py"}, {"file": "b.py"}, "invalid"]
        >>> safe_parse_items(items, parse_item, "mytool")  # doctest: +SKIP
        [MyIssue(file='a.py'), MyIssue(file='b.py')]
    """
    parsed, _skipped = safe_parse_items_with_stats(items, parse_func, tool_name)
    return parsed


def safe_parse_items_with_stats(
    items: list[object],
    parse_func: Callable[[dict[str, object]], IssueT | None],
    tool_name: str = "tool",
) -> tuple[list[IssueT], int]:
    """Safely parse items and return parse failure count.

    Args:
        items: List of items to parse.
        parse_func: Per-item parse function.
        tool_name: Tool name for logging.

    Returns:
        Tuple of parsed issues and count of skipped/unparseable items.
    """
    results: list[IssueT] = []
    skipped_count = 0

    for item in items:
        if not isinstance(item, dict):
            logger.debug(f"Skipping non-dict item in {tool_name} output")
            skipped_count += 1
            continue

        try:
            parsed = parse_func(item)
            if parsed is not None:
                results.append(parsed)
            else:
                skipped_count += 1
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse {tool_name} item: {e}")
            skipped_count += 1
            continue

    return results, skipped_count
