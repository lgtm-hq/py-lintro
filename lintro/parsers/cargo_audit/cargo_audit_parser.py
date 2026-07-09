"""Parser for cargo-audit JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.cargo_audit.cargo_audit_issue import CargoAuditIssue


def _extract_cargo_audit_json(raw_text: str) -> dict[str, Any]:
    """Extract cargo-audit's JSON object from output text.

    This function finds JSON by locating the first '{' and last '}' in the output.
    This approach works because cargo-audit outputs a single top-level JSON object.
    It would not work for tools that output multiple JSON objects or have nested
    structures where the last '}' doesn't correspond to the opening '{'.

    Args:
        raw_text: Raw stdout/stderr text from cargo-audit.

    Returns:
        dict[str, Any]: Parsed JSON object.

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed.
        ValueError: If no JSON object boundaries are found.
    """
    if not raw_text or not raw_text.strip():
        raise json.JSONDecodeError("Empty output", raw_text or "", 0)

    text: str = raw_text.strip()

    # Quick path: if the entire text is JSON
    if text.startswith("{") and text.endswith("}"):
        result: dict[str, Any] = json.loads(text)
        return result

    # Fallback: find JSON boundaries. This works because cargo-audit outputs
    # a single JSON object, so the first '{' and last '}' delimit it correctly.
    start: int = text.find("{")
    end: int = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Could not locate JSON object in cargo-audit output")

    json_str: str = text[start : end + 1]
    parsed: dict[str, Any] = json.loads(json_str)
    return parsed


def extract_cargo_audit_payload(output: str | None) -> dict[str, Any] | None:
    """Extract the parsed JSON object from cargo-audit output.

    Args:
        output: Raw stdout from ``cargo audit --json``.

    Returns:
        The parsed JSON object, or ``None`` when the output is empty or cannot
        be parsed as a JSON object. A ``None`` result on non-empty output
        indicates a parse failure that security callers must treat as a
        non-clean scan (see #1044).
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
