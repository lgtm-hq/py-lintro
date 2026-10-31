"""Parser for Oxlint JSON output.

Handles Oxlint JSON format output from --format json flag.
"""

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import (
    safe_parse_items_with_stats,
    validate_int_field,
    validate_str_field,
)
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue


def parse_oxlint_output(output: str) -> list[OxlintIssue]:
    """Parse Oxlint JSON output into a list of OxlintIssue objects.

    Args:
        output: The raw JSON output from Oxlint.

    Returns:
        List of OxlintIssue objects.
    """
    issues, _failures = parse_oxlint_output_with_stats(output)
    return issues


def parse_oxlint_output_with_stats(output: str) -> tuple[list[OxlintIssue], int]:
    """Parse a report and count unusable reports or diagnostic records.

    Required message and filename fields must be strings; filename must also
    be nonblank. Optional code and severity default to ``""`` and ``"warning"``;
    absent or null coordinates default to ``0`` (unknown).

    Args:
        output: Raw Oxlint output, possibly with text surrounding its JSON.

    Returns:
        Parsed findings and the number of rejected records. An invalid report
        envelope counts as one failure. Non-JSON output without an object
        envelope retains the list parser's empty result; failed-invocation
        classification is owned by the batch runner (#3000).
    """
    if not output:
        return [], 0

    try:
        try:
            # Validate the whole JSON value before tolerating surrounding text.
            # Otherwise slicing braces would accept an object inside an array.
            oxlint_data = json.loads(output)
        except json.JSONDecodeError:
            # Oxlint may print informational text before or after its report.
            json_start = output.find("{")
            json_end = output.rfind("}") + 1
            if json_start == -1:
                return [], 0
            oxlint_data = json.loads(output[json_start:json_end])
    except (ValueError, TypeError) as e:
        logger.debug(f"Error processing Oxlint output: {e}")
        return [], 1

    if not isinstance(oxlint_data, dict):
        logger.debug("Oxlint output is not a dictionary")
        return [], 1

    diagnostics = oxlint_data.get("diagnostics")
    if not isinstance(diagnostics, list):
        logger.debug("Oxlint diagnostics is not a list")
        return [], 1

    return safe_parse_items_with_stats(diagnostics, _parse_diagnostic, "Oxlint")


def _parse_diagnostic(diagnostic: dict[str, Any]) -> OxlintIssue:
    """Parse a single Oxlint diagnostic into an OxlintIssue.

    Args:
        diagnostic: A single diagnostic dictionary from Oxlint output.

    Returns:
        The validated diagnostic.

    Raises:
        ValueError: If a required filename or message is absent or unrenderable.
    """
    # Extract filename
    file_path = diagnostic.get("filename")
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("Oxlint diagnostic requires a nonblank filename string")

    # Extract message and code
    message = diagnostic.get("message")
    if not isinstance(message, str):
        raise ValueError("Oxlint diagnostic requires a message string")
    code = validate_str_field(diagnostic.get("code"), "code")
    severity = validate_str_field(diagnostic.get("severity"), "severity", "warning")
    help_text = diagnostic.get("help")
    if not isinstance(help_text, str):
        help_text = None

    # Extract line and column from labels array
    # labels[0].span contains {offset, length, line, column}
    # Zero means unknown; missing locations must not fabricate a source span.
    line = 0
    column = 0
    labels = diagnostic.get("labels", [])
    if isinstance(labels, list) and len(labels) > 0:
        first_label = labels[0]
        if isinstance(first_label, dict):
            span = first_label.get("span", {})
            if isinstance(span, dict):
                line = validate_int_field(span.get("line"), "line")
                column = validate_int_field(span.get("column"), "column")

    # Oxlint does not currently indicate fixable issues in JSON output
    # Default to False
    fixable = False

    return OxlintIssue(
        file=file_path,
        line=line,
        column=column,
        message=message,
        code=code,
        severity=severity,
        fixable=fixable,
        help=help_text,
    )
