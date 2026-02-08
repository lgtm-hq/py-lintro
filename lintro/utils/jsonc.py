"""JSONC (JSON with Comments) parsing utilities.

Provides functions for stripping JSONC comments and trailing commas,
plus a convenience loader that produces standard Python objects from
JSONC text (as used in tsconfig.json, .markdownlint.jsonc, etc.).
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger


def strip_jsonc_comments(content: str) -> str:
    """Strip JSONC comments from content, preserving strings.

    This function safely removes // and /* */ comments from JSONC content
    while preserving comment-like sequences inside string values.

    Args:
        content: JSONC content as string

    Returns:
        Content with comments stripped

    Note:
        This is a simple implementation that handles most common cases.
        For complex JSONC with nested comments or edge cases, consider
        using a proper JSONC parser library (e.g., json5 or commentjson).
    """
    result: list[str] = []
    i = 0
    content_len = len(content)
    in_string = False
    escape_next = False
    in_block_comment = False

    while i < content_len:
        char = content[i]

        if escape_next:
            escape_next = False
            if not in_block_comment:
                result.append(char)
            i += 1
            continue

        if char == "\\" and in_string:
            escape_next = True
            if not in_block_comment:
                result.append(char)
            i += 1
            continue

        if char == '"' and not in_block_comment:
            in_string = not in_string
            result.append(char)
            i += 1
            continue

        if in_string:
            result.append(char)
            i += 1
            continue

        # Check for block comment start /* ... */
        if i < content_len - 1 and char == "/" and content[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue

        # Check for block comment end */ (when we see *)
        if (
            char == "*"
            and in_block_comment
            and i < content_len - 1
            and content[i + 1] == "/"
        ):
            in_block_comment = False
            i += 2  # Skip both * and /
            continue

        # Check for line comment //
        if (
            i < content_len - 1
            and char == "/"
            and content[i + 1] == "/"
            and not in_block_comment
        ):
            # Skip to end of line
            while i < content_len and content[i] != "\n":
                i += 1
            # Include the newline if present
            if i < content_len:
                result.append("\n")
                i += 1
            continue

        if not in_block_comment:
            result.append(char)

        i += 1

    if in_block_comment:
        logger.warning("Unclosed block comment in JSONC content")

    return "".join(result)


def strip_trailing_commas(content: str) -> str:
    """Strip trailing commas from JSON content.

    Removes trailing commas before closing brackets/braces that are
    invalid in strict JSON but common in JSONC (e.g., tsconfig.json).

    Args:
        content: JSON content with potential trailing commas.

    Returns:
        Content with trailing commas removed.

    Note:
        This is a simple regex-based approach that works for most cases.
        It may incorrectly modify strings containing patterns like ',]'
        but such strings are rare in configuration files.
    """
    # Remove trailing commas before ] or } (with optional whitespace)
    content = re.sub(r",(\s*[\]\}])", r"\1", content)
    return content


def load_jsonc(text: str) -> Any:
    """Parse JSONC text into a Python object.

    Strips comments and trailing commas, then delegates to ``json.loads``.

    Args:
        text: JSONC content as string.

    Returns:
        Parsed Python object (dict, list, str, etc.).
    """
    cleaned = strip_jsonc_comments(text)
    cleaned = strip_trailing_commas(cleaned)
    return json.loads(cleaned)
