"""Parser for RuboCop JSON output.

RuboCop emits rich JSON via ``--format json``. The schema is::

    {
      "metadata": {...},
      "files": [
        {
          "path": "app.rb",
          "offenses": [
            {
              "severity": "convention",
              "message": "Prefer single-quoted strings ...",
              "cop_name": "Style/StringLiterals",
              "corrected": false,
              "correctable": true,
              "location": {
                "start_line": 3, "start_column": 10,
                "last_line": 3, "last_column": 16,
                "line": 3, "column": 10
              }
            }
          ]
        }
      ],
      "summary": {...}
    }

The JSON form is preferred over SARIF because RuboCop bundles no SARIF
formatter, and its native ``correctable`` flag (distinct from ``corrected``)
has no lossless SARIF representation — SARIF only models fix *presence* as a
boolean ``fixes[]`` array. See ``docs/design/sarif-ingestion-evaluation.md``.
"""

from __future__ import annotations

import json

from loguru import logger

from lintro.parsers.base_parser import (
    extract_dict_field,
    extract_int_field,
    extract_str_field,
    safe_parse_items,
)
from lintro.parsers.rubocop.rubocop_issue import RubocopIssue


def _parse_offense(item: dict[str, object]) -> RubocopIssue | None:
    """Parse a single offense dict (with an injected ``path``) into an issue.

    Args:
        item: Offense dictionary from RuboCop JSON output. Must include the
            owning file path under the ``path`` key (injected by the caller).

    Returns:
        RubocopIssue if parsing succeeds, otherwise None.
    """
    path = extract_str_field(item, ["path"])
    loc = extract_dict_field(item, ["location"])

    line = extract_int_field(loc, ["start_line", "line"], default=0) or 0
    column = extract_int_field(loc, ["start_column", "column"], default=0) or 0
    end_line = extract_int_field(loc, ["last_line"], default=line) or line
    end_column = extract_int_field(loc, ["last_column"], default=column) or column

    cop_name = extract_str_field(item, ["cop_name"])
    message = extract_str_field(item, ["message"])
    severity = extract_str_field(item, ["severity"])

    correctable = bool(item.get("correctable", False))
    corrected = bool(item.get("corrected", False))

    department = cop_name.split("/", 1)[0] if "/" in cop_name else ""

    return RubocopIssue(
        file=path,
        line=line,
        column=column,
        code=cop_name,
        message=message,
        severity=severity,
        department=department,
        correctable=correctable,
        corrected=corrected,
        fixable=correctable,
        end_line=end_line,
        end_column=end_column,
    )


def parse_rubocop_output(output: str | None) -> list[RubocopIssue]:
    """Parse RuboCop ``--format json`` output into ``RubocopIssue`` objects.

    Args:
        output: Raw stdout from ``rubocop --format json``.

    Returns:
        list[RubocopIssue]: Parsed offenses across all files. Returns an empty
        list for empty, null, or malformed input.
    """
    if not output or not output.strip():
        return []

    # Decode the leading JSON object and ignore any trailing text. RuboCop's
    # JSON payload is a single object; ``raw_decode`` tolerates trailing
    # diagnostics that may still leak into the stream in some environments.
    start = output.find("{")
    if start == -1:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(output[start:])
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug(f"Failed to parse rubocop JSON output: {e}")
        return []

    if not isinstance(data, dict):
        return []

    files = data.get("files")
    if not isinstance(files, list):
        return []

    offense_items: list[object] = []
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        path = file_entry.get("path")
        offenses = file_entry.get("offenses")
        if not isinstance(offenses, list):
            continue
        for offense in offenses:
            if not isinstance(offense, dict):
                continue
            # Inject the owning path so _parse_offense is self-contained.
            merged = dict(offense)
            merged["path"] = str(path) if path is not None else ""
            offense_items.append(merged)

    return safe_parse_items(offense_items, _parse_offense, "rubocop")
