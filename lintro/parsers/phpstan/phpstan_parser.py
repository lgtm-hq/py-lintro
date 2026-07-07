"""Parser for PHPStan ``--error-format=json`` output.

PHPStan emits a single JSON object of the shape::

    {
      "totals": {"errors": 1, "file_errors": 2},
      "files": {
        "src/App.php": {
          "errors": 2,
          "messages": [
            {
              "message": "Function foo not found.",
              "line": 6,
              "ignorable": true,
              "identifier": "function.notFound",
              "tip": "Learn more at https://phpstan.org/..."
            }
          ]
        }
      },
      "errors": ["Top-level analysis error (no file)"]
    }

The per-file ``messages`` carry a stable ``identifier`` (used for doc URLs)
and an optional ``tip``. The top-level ``errors`` array holds generic,
non-file-specific errors (e.g. configuration problems) as plain strings.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue


def _parse_file_message(
    file_path: str,
    message: dict[str, Any],
) -> PhpstanIssue | None:
    """Parse a single PHPStan file message into a ``PhpstanIssue``.

    Args:
        file_path: Path of the file the message belongs to.
        message: A single entry from a file's ``messages`` array.

    Returns:
        A ``PhpstanIssue`` when the message is well-formed, otherwise ``None``.
    """
    text = message.get("message")
    if not isinstance(text, str) or not text:
        logger.warning("Skipping PHPStan message with missing 'message' text")
        return None

    raw_line = message.get("line")
    line = raw_line if isinstance(raw_line, int) else 0

    identifier = message.get("identifier")
    identifier_str = identifier if isinstance(identifier, str) else ""

    tip = message.get("tip")
    tip_str = tip if isinstance(tip, str) else ""

    ignorable = message.get("ignorable")
    ignorable_bool = bool(ignorable) if isinstance(ignorable, bool) else True

    return PhpstanIssue(
        file=file_path,
        line=line,
        column=0,
        message=text,
        identifier=identifier_str,
        level="error",
        tip=tip_str,
        ignorable=ignorable_bool,
    )


def parse_phpstan_output(output: str | None) -> list[PhpstanIssue]:
    """Parse PHPStan JSON output into ``PhpstanIssue`` objects.

    Args:
        output: JSON string from PHPStan ``--error-format=json`` output, or
            ``None``.

    Returns:
        List of parsed issues. Returns an empty list for ``None``, empty
        string, invalid JSON, or an unexpected data structure.
    """
    if output is None or not output.strip():
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse PHPStan JSON output: {exc}")
        return []

    if not isinstance(data, dict):
        logger.warning(
            "PHPStan output must be a JSON object, got %s",
            type(data).__name__,
        )
        return []

    issues: list[PhpstanIssue] = []

    files = data.get("files", {})
    if isinstance(files, dict):
        for file_path, file_data in files.items():
            if not isinstance(file_data, dict):
                continue
            messages = file_data.get("messages", [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                try:
                    issue = _parse_file_message(str(file_path), message)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(f"Failed to parse PHPStan message: {exc}")
                    continue
                if issue is not None:
                    issues.append(issue)

    # Top-level, non-file-specific errors (e.g. configuration problems).
    generic_errors = data.get("errors", [])
    if isinstance(generic_errors, list):
        for error in generic_errors:
            if not isinstance(error, str) or not error:
                continue
            issues.append(
                PhpstanIssue(
                    file="",
                    line=0,
                    column=0,
                    message=error,
                    identifier="",
                    level="error",
                    tip="",
                    ignorable=False,
                ),
            )

    return issues
