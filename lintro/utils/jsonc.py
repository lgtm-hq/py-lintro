"""JSONC (JSON with Comments) parsing utilities.

Provides functions for stripping JSONC comments and trailing commas,
plus a convenience loader that produces standard Python objects from
JSONC text (as used in tsconfig.json, .markdownlint.jsonc, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
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
    Uses a character-scan to avoid modifying commas inside string literals.

    Args:
        content: JSON content with potential trailing commas.

    Returns:
        Content with trailing commas removed.
    """
    result: list[str] = []
    i = 0
    length = len(content)
    in_string = False
    quote_char = ""
    escape_next = False

    while i < length:
        char = content[i]

        if escape_next:
            escape_next = False
            result.append(char)
            i += 1
            continue

        if char == "\\" and in_string:
            escape_next = True
            result.append(char)
            i += 1
            continue

        if not in_string and char in ('"', "'"):
            in_string = True
            quote_char = char
            result.append(char)
            i += 1
            continue

        if in_string and char == quote_char:
            in_string = False
            result.append(char)
            i += 1
            continue

        if not in_string and char == ",":
            # Look ahead past whitespace for a closing bracket/brace
            j = i + 1
            while j < length and content[j] in (" ", "\t", "\n", "\r"):
                j += 1
            if j < length and content[j] in ("]", "}"):
                # Skip the trailing comma
                i += 1
                continue

        result.append(char)
        i += 1

    return "".join(result)


def extract_type_roots(base_content: Any, base_dir: Path) -> list[str] | None:
    """Extract and resolve typeRoots from parsed tsconfig content.

    Args:
        base_content: Parsed tsconfig content (expected to be a dict).
        base_dir: Directory of the base tsconfig for resolving relative paths.

    Returns:
        Resolved typeRoots list, empty list if explicitly set to ``[]``,
        or ``None`` if typeRoots is not present.
    """
    if not isinstance(base_content, dict):
        return None
    comp_opts = base_content.get("compilerOptions")
    if not isinstance(comp_opts, dict):
        return None
    if "typeRoots" not in comp_opts:
        return None
    base_roots = comp_opts["typeRoots"]
    if not isinstance(base_roots, list):
        return None
    resolved: list[str] = []
    for r in base_roots:
        if not isinstance(r, str):
            continue
        try:
            resolved.append(str((base_dir / r).resolve()))
        except (ValueError, OSError):
            continue
    return resolved


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
