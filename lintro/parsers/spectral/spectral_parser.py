"""Parser for Spectral JSON output.

Handles the JSON document emitted by ``spectral lint --format json``. Each
finding is an object with ``code``, ``path``, ``message``, ``severity``, and a
``range`` describing the source position. Spectral reports zero-based line and
character offsets, which are converted here to lintro's one-based convention.
"""

import json
from typing import Any

from loguru import logger

from lintro.parsers.spectral.spectral_issue import SpectralIssue

# Spectral encodes severity as an integer diagnostic level. Map each level to
# lintro's severity vocabulary; ``hint`` normalizes to INFO downstream.
_SEVERITY_BY_LEVEL: dict[int, str] = {
    0: "error",
    1: "warning",
    2: "info",
    3: "hint",
}


def parse_spectral_output(output: str | None) -> list[SpectralIssue]:
    """Parse Spectral JSON output into a list of SpectralIssue objects.

    Args:
        output: The raw JSON output from ``spectral lint --format json``.

    Returns:
        List of SpectralIssue objects. Returns an empty list for empty,
        null, or malformed input.
    """
    issues: list[SpectralIssue] = []

    if not output or not output.strip():
        return issues

    try:
        # Spectral may emit non-JSON preamble (e.g. a missing-ruleset warning)
        # before the JSON array; slice from the first bracket to be safe.
        json_start = output.find("[")
        json_end = output.rfind("]") + 1
        if json_start == -1 or json_end == 0:
            return issues
        data: Any = json.loads(output[json_start:json_end])
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse Spectral JSON output: {e}")
        return issues
    except (ValueError, TypeError) as e:
        logger.debug(f"Error processing Spectral output: {e}")
        return issues

    if not isinstance(data, list):
        logger.debug("Spectral output is not a list")
        return issues

    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            issue = _parse_entry(entry)
            if issue is not None:
                issues.append(issue)
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse Spectral finding: {e}")
            continue

    return issues


def _parse_entry(entry: dict[str, Any]) -> SpectralIssue | None:
    """Parse a single Spectral finding into a SpectralIssue.

    Args:
        entry: A single finding object from Spectral's JSON array.

    Returns:
        SpectralIssue if parsing succeeds, otherwise None.
    """
    code = str(entry.get("code", ""))
    message = str(entry.get("message", ""))
    file_path = str(entry.get("source", ""))

    # Severity: integer diagnostic level mapped to a lintro severity string.
    raw_severity = entry.get("severity", 1)
    severity = _SEVERITY_BY_LEVEL.get(
        raw_severity if isinstance(raw_severity, int) else 1,
        "warning",
    )

    # JSON path: array of segments pointing at the offending node. Joined with
    # "." for display; empty for document-level findings.
    raw_path = entry.get("path", [])
    if isinstance(raw_path, list):
        path = ".".join(str(segment) for segment in raw_path)
    else:
        path = ""

    # Range: zero-based line/character offsets -> one-based line/column.
    line = 1
    column = 1
    range_obj = entry.get("range", {})
    if isinstance(range_obj, dict):
        start = range_obj.get("start", {})
        if isinstance(start, dict):
            line = int(start.get("line", 0)) + 1
            column = int(start.get("character", 0)) + 1

    return SpectralIssue(
        file=file_path,
        line=line,
        column=column,
        message=message,
        code=code,
        severity=severity,
        path=path,
    )
