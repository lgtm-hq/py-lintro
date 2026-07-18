"""Parser for ktlint JSON output.

This module provides parsing functionality for ktlint's ``--reporter=json``
output format. ktlint is an anti-bikeshedding Kotlin linter with a built-in
formatter that identifies style violations in Kotlin (``.kt``) and Kotlin
Script (``.kts``) files.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.ktlint.ktlint_issue import KtlintIssue


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


def _strip_to_json_array(output: str) -> str:
    """Return ``output`` starting at the top-level JSON array.

    ktlint logs to stdout at ``warn`` level (e.g. "Lint has found errors
    than can be autocorrected using 'ktlint --format'") *before* the JSON
    report, which would break a naive ``json.loads``. lintro invokes ktlint
    with ``--log-level=error`` to suppress this, but the parser stays robust
    to any leading log lines. ktlint's log lines are prefixed with a
    timestamp (e.g. ``10:00:00.000 [main] WARN ...``) and never begin with
    ``[``, whereas the JSON report always begins on a line whose first
    non-space character is ``[``. Dropping leading lines until that marker is
    found avoids being fooled by a bracket such as ``[main]`` inside a log
    line.

    Args:
        output: Raw stdout captured from ktlint.

    Returns:
        The substring beginning at the JSON array, or the original string
        when no such line is present.
    """
    if output.lstrip().startswith("["):
        return output.lstrip()

    lines = output.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            return "".join(lines[index:])
    return output


def parse_ktlint_output(output: str | None) -> list[KtlintIssue]:
    r"""Parse ktlint JSON output into a list of KtlintIssue objects.

    ktlint emits JSON in the following shape when run with
    ``--reporter=json``::

        [
          {
            "file": "src/Foo.kt",
            "errors": [
              {
                "line": 2,
                "column": 15,
                "message": "Unexpected spacing before \\":\\"",
                "rule": "standard:colon-spacing"
              }
            ]
          }
        ]

    Args:
        output: The raw JSON output from ktlint, or None.

    Returns:
        List of KtlintIssue objects (empty for clean, empty, or malformed
        input).
    """
    issues: list[KtlintIssue] = []

    if output is None or not output.strip():
        return issues

    try:
        parsed = json.loads(_strip_to_json_array(output))
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse ktlint output as JSON: {e}")
        return issues

    # ktlint always emits a top-level array of per-file entries.
    if not isinstance(parsed, list):
        return issues

    for file_entry in parsed:
        if not isinstance(file_entry, dict):
            continue

        file_path: str = str(file_entry.get("file", ""))
        errors = file_entry.get("errors", [])
        if not isinstance(errors, list):
            continue

        for error in errors:
            if not isinstance(error, dict):
                continue

            issues.append(
                KtlintIssue(
                    file=file_path,
                    line=_safe_int(error.get("line", 0)),
                    column=_safe_int(error.get("column", 0)),
                    message=str(error.get("message", "")),
                    rule=str(error.get("rule", "")),
                ),
            )

    return issues
