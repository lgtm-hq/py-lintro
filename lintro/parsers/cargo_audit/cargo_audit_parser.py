"""Parser for cargo-audit JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.cargo_audit.cargo_audit_issue import CargoAuditIssue


def _find_report_start(text: str) -> int:
    """Locate an object without losing an enclosing array.

    Balanced brackets before the object can be warning prose, such as ``[WARN]``
    or ``note [1]``. Any array still open at the object belongs to its envelope.
    Quoted brackets inside an array must not change that nesting.

    Args:
        text: Output text that may contain a JSON report.

    Returns:
        The object or enclosing array's index, or -1 when neither is found.
    """
    array_starts: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"' and array_starts:
            in_string = True
        elif char == "[":
            array_starts.append(index)
        elif char == "]" and array_starts:
            array_starts.pop()
        elif char == "{":
            return array_starts[0] if array_starts else index
    return array_starts[0] if array_starts else -1


def _extract_cargo_audit_json(raw_text: str) -> object:
    """Extract cargo-audit's JSON object from output text.

    Parse the whole text first so a valid JSON value retains its top-level type.
    For output with surrounding text, skip balanced bracketed prose but keep
    any enclosing array so an array cannot become its inner object.

    Args:
        raw_text: Raw stdout/stderr text from cargo-audit.

    Returns:
        Parsed JSON value, expected to be an object.

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed.
        ValueError: If no JSON container boundaries are found.
    """
    if not raw_text or not raw_text.strip():
        raise json.JSONDecodeError("Empty output", raw_text or "", 0)

    text: str = raw_text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Keep the outer container intact for top-level type validation.
    start = _find_report_start(text)
    if start == -1:
        raise ValueError("Could not locate JSON container in cargo-audit output")

    end = text.rfind("}" if text[start] == "{" else "]")
    if end < start:
        raise ValueError("Could not locate JSON container in cargo-audit output")

    json_str: str = text[start : end + 1]
    return json.loads(json_str)


def extract_cargo_audit_payload(output: str | None) -> dict[str, Any] | None:
    """Extract the parsed JSON object from cargo-audit output.

    Args:
        output: Raw stdout from ``cargo audit --json``.

    Returns:
        The parsed JSON object with a ``vulnerabilities.list`` list, or ``None``
        when the output is empty, invalid JSON, or lacks that report schema.
        A ``None`` result indicates a parse failure that security callers must
        treat as a non-clean scan (see #1044 and #3003). An empty list is valid.
    """
    if not output or not output.strip():
        return None

    try:
        data = _extract_cargo_audit_json(output)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse cargo-audit JSON output: {e}")
        return None

    if not isinstance(data, dict):
        logger.warning("cargo-audit output is not a dictionary")
        return None

    vulnerabilities = data.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict) or not isinstance(
        vulnerabilities.get("list"),
        list,
    ):
        logger.warning("cargo-audit output missing a valid 'vulnerabilities.list' list")
        return None

    return data


def _normalize_severity(severity: str | None) -> str:
    """Normalize severity level to uppercase standard format.

    Args:
        severity: Raw severity string from cargo-audit.

    Returns:
        Normalized severity string (UNKNOWN, LOW, MEDIUM, HIGH, CRITICAL).
    """
    if not severity:
        return "UNKNOWN"

    normalized = severity.upper().strip()

    # Map common variations (including RustSec's "none" severity level)
    severity_map = {
        "LOW": "LOW",
        "MEDIUM": "MEDIUM",
        "HIGH": "HIGH",
        "CRITICAL": "CRITICAL",
        "INFO": "LOW",
        "INFORMATIONAL": "LOW",
        "MODERATE": "MEDIUM",
        "SEVERE": "HIGH",
        "NONE": "LOW",
    }

    return severity_map.get(normalized, "UNKNOWN")


def parse_cargo_audit_output(
    output: str | None,
    data: dict[str, Any] | None = None,
) -> list[CargoAuditIssue]:
    """Parse cargo-audit JSON output into CargoAuditIssue objects.

    Args:
        output: Raw JSON output from cargo-audit --json command.
        data: Pre-parsed JSON payload when already extracted from ``output``.

    Returns:
        List of parsed security vulnerability issues.
    """
    if data is None:
        data = extract_cargo_audit_payload(output)
    if data is None:
        return []

    # Extract vulnerabilities from the nested structure
    vulnerabilities = data.get("vulnerabilities", {})
    if not isinstance(vulnerabilities, dict):
        return []

    vuln_list = vulnerabilities.get("list", [])
    if not isinstance(vuln_list, list):
        return []

    issues: list[CargoAuditIssue] = []

    for vuln in vuln_list:
        if not isinstance(vuln, dict):
            continue

        try:
            # Extract advisory information
            advisory = vuln.get("advisory", {})
            if not isinstance(advisory, dict):
                continue

            advisory_id = validate_str_field(
                advisory.get("id"),
                "advisory_id",
                log_warning=True,
            )

            # Skip if no advisory ID
            if not advisory_id:
                logger.warning("Skipping vulnerability with empty advisory ID")
                continue

            title = validate_str_field(advisory.get("title"), "title")
            description = validate_str_field(advisory.get("description"), "description")
            severity = _normalize_severity(advisory.get("severity"))
            url = validate_str_field(advisory.get("url"), "url")

            # Extract package information
            package = vuln.get("package", {})
            if not isinstance(package, dict):
                package = {}

            package_name = validate_str_field(package.get("name"), "package_name")
            package_version = validate_str_field(
                package.get("version"),
                "package_version",
            )

            issue = CargoAuditIssue(
                file="Cargo.lock",
                line=0,  # cargo-audit doesn't provide line numbers
                column=0,
                advisory_id=advisory_id,
                package_name=package_name,
                package_version=package_version,
                severity=severity,
                title=title,
                description=description,
                url=url,
            )
            issues.append(issue)

        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse cargo-audit vulnerability: {e}")
            continue

    return issues
