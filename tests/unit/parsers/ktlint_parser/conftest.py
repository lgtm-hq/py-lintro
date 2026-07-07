"""Shared fixtures and utilities for ktlint parser tests."""

from __future__ import annotations

import json
from typing import Any

# Real ktlint --reporter=json output captured from ktlint 1.8.0 for a Kotlin
# source file with four violations (one of which is not auto-correctable).
REAL_KTLINT_OUTPUT: str = json.dumps(
    [
        {
            "file": "src/Example.kt",
            "errors": [
                {
                    "line": 1,
                    "column": 1,
                    "message": (
                        "File 'Example.kt' contains a single class, and possibly "
                        "related top level declarations for that class. The file "
                        "should be named after the class, 'Foo.kt'"
                    ),
                    "rule": "standard:filename",
                },
                {
                    "line": 2,
                    "column": 14,
                    "message": "Unexpected whitespace",
                    "rule": "standard:function-return-type-spacing",
                },
                {
                    "line": 2,
                    "column": 15,
                    "message": 'Unexpected spacing before ":"',
                    "rule": "standard:colon-spacing",
                },
                {
                    "line": 3,
                    "column": 14,
                    "message": 'Missing spacing around "="',
                    "rule": "standard:op-spacing",
                },
            ],
        },
    ],
)


def make_error(
    *,
    line: int = 2,
    column: int = 15,
    message: str = 'Unexpected spacing before ":"',
    rule: str = "standard:colon-spacing",
) -> dict[str, Any]:
    """Create a single ktlint error dictionary with sensible defaults.

    Args:
        line: The 1-based line number.
        column: The 1-based column number.
        message: The human-readable message.
        rule: The ktlint rule id.

    Returns:
        Dictionary representing a single ktlint error.
    """
    return {"line": line, "column": column, "message": message, "rule": rule}


def make_ktlint_output(
    file_entries: list[dict[str, Any]],
    *,
    log_prefix: str = "",
) -> str:
    """Serialize per-file entries into ktlint JSON output.

    Args:
        file_entries: List of ``{"file": ..., "errors": [...]}`` dictionaries.
        log_prefix: Optional leading log text to prepend (mimics ktlint's
            warn-level stdout logging ahead of the JSON report).

    Returns:
        JSON string, optionally prefixed with log text.
    """
    return log_prefix + json.dumps(file_entries)


def make_file_entry(
    *,
    file: str = "src/Example.kt",
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a per-file ktlint entry.

    Args:
        file: The file path.
        errors: List of error dictionaries (defaults to a single error).

    Returns:
        Dictionary representing a ktlint per-file entry.
    """
    return {"file": file, "errors": errors if errors is not None else [make_error()]}
