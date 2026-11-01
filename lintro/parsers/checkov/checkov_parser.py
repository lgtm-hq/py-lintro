"""Checkov output parser for Infrastructure-as-Code misconfigurations."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.checkov.checkov_issue import CheckovIssue

#: Synthetic check id for a file checkov listed under ``results.parsing_errors``.
#: Not a policy: checkov could not read the file, so *no* policy ran against it.
#: Surfacing it as an issue is what keeps an unreadable ``.tf`` from being
#: reported as a clean security scan — checkov derives its exit code from failed
#: checks alone, so a parse error alone still exits 0 with an empty
#: ``failed_checks``.
CHECKOV_PARSE_ERROR_CODE: str = "CKV_PARSE_ERROR"


def _is_empty_summary(data: dict[str, Any]) -> bool:
    """Recognize Checkov's summary-only output when no framework has checks.

    Args:
        data: Decoded top-level report object.

    Returns:
        Whether the object is the zero summary emitted by runner_registry.
    """
    counts = ("passed", "failed", "skipped", "parsing_errors", "resource_count")
    return (
        "results" not in data
        and isinstance(data.get("checkov_version"), str)
        and bool(data["checkov_version"].strip())
        and all(type(data.get(key)) is int and data[key] == 0 for key in counts)
    )


def parse_checkov_output(output: str | None) -> list[CheckovIssue]:
    """Return Checkov findings through the legacy list API.

    Args:
        output: Raw JSON formatter output.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_checkov_output_with_stats(output)
    return issues


def parse_checkov_output_with_stats(
    output: str | None,
) -> tuple[list[CheckovIssue], int]:
    """Parse Checkov JSON output into ``CheckovIssue`` objects.

    Failed checks and parsing errors are surfaced as issues; passed and
    skipped checks are ignored. The parser is defensive against malformed JSON
    and unexpected structures, counting failures rather than raising.

    Args:
        output: Raw JSON string from ``checkov --output json``. May be
            ``None`` or empty.

    Returns:
        Usable findings and the count of invalid report containers or records.
        Single-framework objects, multi-framework lists and the explicit
        zero-check summary are supported.
    """
    if not output or not output.strip():
        return [], 0

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse checkov JSON output: {}", exc)
        return [], 1

    if isinstance(data, dict):
        if _is_empty_summary(data):
            return [], 0
        blocks = [data]
    elif isinstance(data, list):
        blocks = data
    else:
        return [], 1

    issues: list[CheckovIssue] = []
    failures = 0
    for block in blocks:
        block_issues, block_failures = _parse_report_block(block)
        issues.extend(block_issues)
        failures += block_failures
    return issues, failures


def _parse_report_block(block: Any) -> tuple[list[CheckovIssue], int]:
    """Validate one framework without discarding usable sibling reports.

    Args:
        block: One framework report from Checkov's object or array envelope.

    Returns:
        Usable findings and the number of invalid containers or checks.
    """
    if not isinstance(block, dict) or not isinstance(block.get("results"), dict):
        return [], 1
    results = block["results"]
    issues: list[CheckovIssue] = []
    failed_checks = results.get("failed_checks")
    if not isinstance(failed_checks, list):
        return _parse_parsing_errors(results.get("parsing_errors")), 1

    failures = 0
    for check in failed_checks:
        issue = _parse_failed_check(check) if isinstance(check, dict) else None
        if issue is None:
            failures += 1
        else:
            issues.append(issue)
    issues.extend(_parse_parsing_errors(results.get("parsing_errors")))
    return issues, failures


def _parse_parsing_errors(parsing_errors: Any) -> list[CheckovIssue]:
    """Turn ``results.parsing_errors`` entries into issues.

    Checkov lists files it could not read here and still exits 0, so a report
    whose ``failed_checks`` is empty but whose ``parsing_errors`` is not would
    otherwise be indistinguishable from a clean scan — a fail-open on a
    security path, for files no policy ever ran against.

    Args:
        parsing_errors: The report's ``parsing_errors`` value; checkov emits a
            list of file paths, but any shape is tolerated.

    Returns:
        list[CheckovIssue]: One issue per unreadable file.
    """
    if not isinstance(parsing_errors, list):
        return []

    issues: list[CheckovIssue] = []
    for entry in parsing_errors:
        if not isinstance(entry, str) or not entry.strip():
            continue
        issues.append(
            CheckovIssue(
                file=entry,
                line=0,
                check_id=CHECKOV_PARSE_ERROR_CODE,
                check_name=(
                    "Checkov could not parse this file, so no policy was "
                    "evaluated against it"
                ),
                resource="",
            ),
        )
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
        file_path = validate_str_field(
            check.get("file_abs_path") or check.get("file_path"),
            "file_path",
        )

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
