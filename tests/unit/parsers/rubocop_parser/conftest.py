"""Shared fixtures and helpers for RuboCop parser tests.

The helpers build JSON matching RuboCop's ``--format json`` schema, captured
from a real ``rubocop 1.88.1`` run so tests exercise the actual field shapes.
"""

from __future__ import annotations

import json
from typing import Any


def make_offense(
    *,
    cop_name: str = "Style/StringLiterals",
    severity: str = "convention",
    message: str = "Prefer single-quoted strings.",
    correctable: bool = True,
    corrected: bool = False,
    start_line: int = 3,
    start_column: int = 10,
    last_line: int | None = None,
    last_column: int | None = None,
) -> dict[str, Any]:
    """Build a single RuboCop offense dictionary.

    Args:
        cop_name: The cop identifier (e.g., "Layout/SpaceInsideParens").
        severity: Native severity (info, refactor, convention, warning,
            error, fatal).
        message: The offense message.
        correctable: Whether RuboCop can autocorrect the offense.
        corrected: Whether RuboCop already corrected the offense.
        start_line: Offense start line.
        start_column: Offense start column.
        last_line: Offense end line (defaults to ``start_line``).
        last_column: Offense end column (defaults to ``start_column``).

    Returns:
        A dictionary shaped like a RuboCop offense entry.
    """
    return {
        "severity": severity,
        "message": message,
        "cop_name": cop_name,
        "corrected": corrected,
        "correctable": correctable,
        "location": {
            "start_line": start_line,
            "start_column": start_column,
            "last_line": last_line if last_line is not None else start_line,
            "last_column": last_column if last_column is not None else start_column,
            "line": start_line,
            "column": start_column,
        },
    }


def make_rubocop_output(
    files: dict[str, list[dict[str, Any]]],
    *,
    rubocop_version: str = "1.88.1",
) -> str:
    """Build a full RuboCop JSON output string.

    Args:
        files: Mapping of file path to a list of offense dictionaries.
        rubocop_version: Version reported in the metadata block.

    Returns:
        JSON string matching RuboCop ``--format json`` output.
    """
    offense_count = sum(len(offenses) for offenses in files.values())
    payload = {
        "metadata": {
            "rubocop_version": rubocop_version,
            "ruby_engine": "ruby",
            "ruby_version": "3.4.7",
        },
        "files": [
            {"path": path, "offenses": offenses} for path, offenses in files.items()
        ],
        "summary": {
            "offense_count": offense_count,
            "target_file_count": len(files),
            "inspected_file_count": len(files),
        },
    }
    return json.dumps(payload)
