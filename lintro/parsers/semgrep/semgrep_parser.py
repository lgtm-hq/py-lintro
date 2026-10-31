"""Semgrep output parser for security and code quality findings."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import (
    extract_dict_field,
    extract_int_field,
    extract_str_field,
    validate_str_field,
)
from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue


def _parse_single_result(result: dict[str, Any]) -> SemgrepIssue | None:
    """Parse a single Semgrep result into a SemgrepIssue.

    Args:
        result: Dictionary containing a single Semgrep result.

    Returns:
        SemgrepIssue if parsing succeeds, None otherwise.
    """
    # Extract required fields
    check_id = validate_str_field(
        value=result.get("check_id"),
        field_name="check_id",
        log_warning=True,
    )
    path = validate_str_field(
        value=result.get("path"),
        field_name="path",
        log_warning=True,
    )

    # Skip if required fields are missing
    if not check_id or not path:
        logger.warning("Skipping issue with missing check_id or path")
        return None

    # Extract start position (nested structure)
    start = extract_dict_field(data=result, candidates=["start"])
    line = extract_int_field(data=start, candidates=["line"], default=0)
    column = extract_int_field(data=start, candidates=["col"], default=0)

    # Skip if line is missing (required field)
    if line is None or line == 0:
        logger.warning("Skipping issue with missing or invalid line number")
        return None

    # Extract end position (nested structure)
    end = extract_dict_field(data=result, candidates=["end"])
    end_line = extract_int_field(data=end, candidates=["line"], default=0)
    end_column = extract_int_field(data=end, candidates=["col"], default=0)

    # Extract extra fields (nested structure)
    extra = extract_dict_field(data=result, candidates=["extra"])
    message = extract_str_field(data=extra, candidates=["message"], default="")
    severity = extract_str_field(data=extra, candidates=["severity"], default="WARNING")

    # Extract metadata (nested inside extra)
    metadata = extract_dict_field(data=extra, candidates=["metadata"])
    if metadata is None or not isinstance(metadata, dict):
        metadata = {}
    category = extract_str_field(data=metadata, candidates=["category"], default="")

    # Extract CWE IDs (may be a list or None)
    cwe_raw = metadata.get("cwe")
    cwe: list[str] | None = None
    if isinstance(cwe_raw, list):
        cwe = [str(c) for c in cwe_raw if c is not None]
    elif isinstance(cwe_raw, str):
        cwe = [cwe_raw]

    return SemgrepIssue(
        file=path,
        line=line,
        column=column or 0,
        message=message,
        check_id=check_id,
        end_line=end_line or 0,
        end_column=end_column or 0,
        severity=severity.upper() if severity else "WARNING",
        category=category,
        cwe=cwe,
        metadata=metadata if metadata else None,
    )


def parse_semgrep_output(output: str | None) -> list[SemgrepIssue]:
    """Parse Semgrep JSON output into SemgrepIssue objects.

    Args:
        output: JSON string from Semgrep output, or None.

    Returns:
        List of parsed security/code quality issues. Returns empty list for
        None, empty string, invalid JSON, or a non-object JSON value.

    Raises:
        ValueError: If the JSON object has no results array or it is not a list.
    """
    if output is None or not output.strip():
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse Semgrep JSON output: {e}")
        return []

    if not isinstance(data, dict):
        logger.warning(
            "Semgrep output must be a JSON object, got %s",
            type(data).__name__,
        )
        return []

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError(
            f"Semgrep results must be a list, got {type(results).__name__}",
        )

    issues: list[SemgrepIssue] = []

    for result in results:
        if not isinstance(result, dict):
            logger.debug("Skipping non-dict item in Semgrep results")
            continue

        try:
            issue = _parse_single_result(result=result)
            if issue is not None:
                issues.append(issue)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse Semgrep issue: {e}")
            continue

    return issues
