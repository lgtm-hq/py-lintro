"""Parser for SQLFluff JSON output.

This module provides functions to parse SQLFluff JSON output into
SqlfluffIssue objects.

SQLFluff JSON output has a nested structure with a "violations" array per file:
[
  {
    "filepath": "query.sql",
    "violations": [
      {
        "start_line_no": 1,
        "start_line_pos": 1,
        "end_line_no": 1,
        "end_line_pos": 6,
        "code": "L010",
        "description": "Keywords must be upper case.",
        "name": "capitalisation.keywords"
      }
    ]
  }
]
"""

from __future__ import annotations

import json

from loguru import logger

from lintro.parsers.base_parser import (
    extract_int_field,
    extract_str_field,
    safe_parse_items_with_stats,
)
from lintro.parsers.sqlfluff.sqlfluff_issue import SqlfluffIssue


def _parse_sqlfluff_violation(
    violation: dict[str, object],
    filepath: str,
) -> SqlfluffIssue | None:
    """Parse a single SQLFluff violation into a SqlfluffIssue object.

    Args:
        violation: Dictionary containing violation data from SQLFluff JSON output.
        filepath: File path for the violation.

    Returns:
        SqlfluffIssue object if parsing succeeds, None otherwise.
    """
    line = extract_int_field(violation, ["start_line_no", "line"], default=0) or 0
    column = extract_int_field(violation, ["start_line_pos", "column"], default=0) or 0
    end_line = extract_int_field(violation, ["end_line_no"], default=None)
    end_column = extract_int_field(violation, ["end_line_pos"], default=None)

    code = extract_str_field(violation, ["code"]) or ""
    rule_name = extract_str_field(violation, ["name", "rule_name"]) or ""
    message = extract_str_field(violation, ["description", "message"]) or ""

    return SqlfluffIssue(
        file=filepath,
        line=line,
        column=column,
        code=code,
        rule_name=rule_name,
        message=message,
        end_line=end_line,
        end_column=end_column,
    )


def parse_sqlfluff_output(output: str | None) -> list[SqlfluffIssue]:
    """Return SQLFluff findings through the legacy list API.

    Args:
        output: Raw JSON formatter output.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_sqlfluff_output_with_stats(output)
    return issues


def parse_sqlfluff_output_with_stats(
    output: str | None,
) -> tuple[list[SqlfluffIssue], int]:
    """Parse SQLFluff JSON output into SqlfluffIssue objects.

    Supports SQLFluff's nested JSON structure where each file entry contains
    a "violations" array.

    Args:
        output: Raw output from `sqlfluff lint --format=json`.

    Returns:
        Usable findings and the count of invalid report containers or records.
    """
    if not output or not output.strip():
        return [], 0

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug(f"SQLFluff JSON parsing failed: {e}")
        return [], 1

    if not isinstance(data, list):
        logger.debug(f"SQLFluff output is not a list: {type(data).__name__}")
        return [], 1

    issues: list[SqlfluffIssue] = []
    failures = 0

    for file_entry in data:
        if not isinstance(file_entry, dict):
            logger.debug("Skipping non-dict file entry in SQLFluff output")
            failures += 1
            continue

        filepath = extract_str_field(file_entry, ["filepath", "file"])
        violations = file_entry.get("violations")

        if not isinstance(violations, list):
            failures += 1
            continue

        # Parse violations for this file
        def parse_with_filepath(
            violation: dict[str, object],
            fp: str = filepath,  # Capture by value to avoid B023
        ) -> SqlfluffIssue | None:
            return _parse_sqlfluff_violation(violation=violation, filepath=fp)

        file_issues, file_failures = safe_parse_items_with_stats(
            items=violations,
            parse_func=parse_with_filepath,
            tool_name="sqlfluff",
        )
        issues.extend(file_issues)
        failures += file_failures

    return issues, failures
