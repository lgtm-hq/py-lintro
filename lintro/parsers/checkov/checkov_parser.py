"""Checkov output parser for Infrastructure-as-Code misconfigurations."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.checkov.checkov_issue import CheckovIssue


def _iter_report_blocks(data: Any) -> list[dict[str, Any]]:
    """Normalize Checkov JSON into a list of per-framework report blocks.

    Checkov emits a single object when one framework runs and a list of
    objects when several frameworks (e.g. terraform + secrets) run against the
    same paths. This helper flattens both shapes into a list of report dicts.

    Args:
        data: Parsed Checkov JSON (object or list of objects).

    Returns:
        list[dict[str, Any]]: Report blocks that carry a ``results`` mapping.
    """
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [block for block in data if isinstance(block, dict)]
    return []


def parse_checkov_output(output: str | None) -> list[CheckovIssue]:
    """Parse Checkov JSON output into ``CheckovIssue`` objects.

    Only failed checks are surfaced as issues; passed and skipped checks are
    ignored. The parser is defensive against malformed JSON and unexpected
    structures, returning an empty list rather than raising.

    Args:
        output: Raw JSON string from ``checkov --output json``. May be
            ``None`` or empty.

    Returns:
        list[CheckovIssue]: Parsed failed checks. Empty when there are no
            failures or the input cannot be parsed.
    """
    if not output or not output.strip():
        return []

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse checkov JSON output: {}", exc)
        return []

    issues: list[CheckovIssue] = []

    for block in _iter_report_blocks(data):
        results = block.get("results")
        if not isinstance(results, dict):
            continue

        failed_checks = results.get("failed_checks")
        if not isinstance(failed_checks, list):
            continue

        for check in failed_checks:
            if not isinstance(check, dict):
                continue
            issue = _parse_failed_check(check)
            if issue is not None:
                issues.append(issue)

    return issues


def _parse_failed_check(check: dict[str, Any]) -> CheckovIssue | None:
    """Convert a single failed-check record into a ``CheckovIssue``.

    Args:
        check: One entry from ``results.failed_checks``.

    Returns:
        CheckovIssue | None: The parsed issue, or ``None`` when required
            fields are missing or malformed.
    """
    try:
        check_id = validate_str_field(check.get("check_id"), "check_id")
        file_path = validate_str_field(check.get("file_path"), "file_path")

        if not check_id or not file_path:
            logger.warning("Skipping checkov issue missing check_id or file_path")
            return None

        check_name = validate_str_field(check.get("check_name"), "check_name")
        resource = validate_str_field(check.get("resource"), "resource")

        line_range = check.get("file_line_range")
        start_line = 0
        end_line: int | None = None
        if isinstance(line_range, list) and line_range:
            first = line_range[0]
            if isinstance(first, int) and not isinstance(first, bool):
                start_line = first
            if len(line_range) > 1:
                last = line_range[1]
                if isinstance(last, int) and not isinstance(last, bool):
                    end_line = last

        severity_raw = check.get("severity")
        severity = severity_raw if isinstance(severity_raw, str) else None

        guideline_raw = check.get("guideline")
        guideline = guideline_raw if isinstance(guideline_raw, str) else None

        check_class_raw = check.get("check_class")
        check_class = check_class_raw if isinstance(check_class_raw, str) else None

        return CheckovIssue(
            file=file_path,
            line=start_line,
            check_id=check_id,
            check_name=check_name,
            resource=resource,
            check_class=check_class,
            severity=severity,
            guideline=guideline,
            end_line=end_line,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Failed to parse checkov issue: {}", exc)
        return None
