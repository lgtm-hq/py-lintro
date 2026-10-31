"""Parser for cargo-deny JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.cargo_deny.cargo_deny_issue import CargoDenyIssue


def _extract_crate_info(labels: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Extract crate name and version from diagnostic labels.

    Args:
        labels: List of label objects from the diagnostic.

    Returns:
        Tuple of (crate_name, crate_version).
    """
    for label in labels:
        message = label.get("message", "")
        if isinstance(message, str) and message.startswith("crate "):
            # Format: "crate foo" or "crate foo@1.0.0"
            crate_info = message[6:].strip()  # Remove "crate " prefix
            if not crate_info:
                return None, None
            if "@" in crate_info:
                name, version = crate_info.split("@", 1)
                return name.strip() or None, version.strip() or None
            return crate_info, None
    return None, None


def _parse_diagnostic(item: dict[str, Any]) -> CargoDenyIssue | None:
    """Parse a diagnostic message from cargo-deny output.

    All non-empty string severities, including note/help, are retained.
    A missing code is valid; None always indicates a malformed record.

    Args:
        item: A diagnostic JSON object.

    Returns:
        CargoDenyIssue or None if parsing fails.
    """
    try:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            return None

        severity = fields.get("severity", "")
        code = fields.get("code", "")
        message = fields.get("message", "")
        labels = fields.get("labels", [])

        if not isinstance(severity, str) or not severity:
            return None
        if not isinstance(labels, list) or any(
            not isinstance(label, dict) for label in labels
        ):
            return None

        # Normalize severity to lowercase
        severity = severity.lower()

        # Extract crate info from labels
        crate_name, crate_version = _extract_crate_info(labels)

        return CargoDenyIssue(
            file="Cargo.toml",  # cargo-deny operates at project level
            line=0,  # No line information available
            column=0,
            code=str(code) if code else "",
            severity=severity,
            message=str(message) if message else "",
            crate_name=crate_name,
            crate_version=crate_version,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse cargo-deny diagnostic: {e}")
        return None


def _parse_advisory(item: dict[str, Any]) -> CargoDenyIssue | None:
    """Parse an advisory message from cargo-deny output.

    Args:
        item: An advisory JSON object.

    Returns:
        CargoDenyIssue or None if parsing fails.
    """
    try:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            return None

        advisory = fields.get("advisory")
        if not isinstance(advisory, dict):
            return None

        advisory_id = advisory.get("id", "")
        advisory_severity = advisory.get("severity", "")

        # Extract patched versions
        versions = fields.get("versions", {})
        patched = None
        if isinstance(versions, dict):
            patched_list = versions.get("patched", [])
            if isinstance(patched_list, list):
                patched = [str(v) for v in patched_list if v]

        # Get crate information from the package field
        package = fields.get("package", {})
        crate_name = None
        crate_version = None
        if isinstance(package, dict):
            crate_name = package.get("name")
            crate_version = package.get("version")

        return CargoDenyIssue(
            file="Cargo.toml",
            line=0,
            column=0,
            code=str(advisory_id) if advisory_id else "ADVISORY",
            severity="error",  # Advisories are treated as errors
            crate_name=str(crate_name) if crate_name else None,
            crate_version=str(crate_version) if crate_version else None,
            advisory_id=str(advisory_id) if advisory_id else None,
            advisory_severity=str(advisory_severity) if advisory_severity else None,
            patched_versions=patched,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse cargo-deny advisory: {e}")
        return None


def _parse_record(item: dict[str, Any]) -> tuple[CargoDenyIssue | None, bool]:
    """Parse a finding or distinguish an ignored record from a failure.

    Args:
        item: One decoded cargo-deny JSON record.

    Returns:
        The parsed issue and whether the finding record is malformed.
    """
    item_type = item.get("type")
    if item_type == "diagnostic":
        parsed = _parse_diagnostic(item)
    elif item_type == "advisory":
        parsed = _parse_advisory(item)
    elif item_type in ("summary", "log", "build"):
        return None, False
    else:
        # Future informational record types may be ignored, but a missing or
        # invalid type, or finding fields under an unknown type, loses a finding.
        fields = item.get("fields")
        finding_fields = isinstance(fields, dict) and (
            "severity" in fields or "advisory" in fields
        )
        invalid_type = not isinstance(item_type, str) or not item_type
        return None, invalid_type or finding_fields

    return parsed, parsed is None


def parse_cargo_deny_output_with_stats(output: str) -> tuple[list[CargoDenyIssue], int]:
    """Parse cargo-deny JSON Lines output into CargoDenyIssue objects.

    cargo-deny outputs JSON Lines format (one JSON object per line) when
    using --format json. Each line can be:
    - A diagnostic message with type "diagnostic"
    - An advisory message with type "advisory" (compatibility support)
    - An informational record such as "log" or "summary", which is ignored

    Args:
        output: Raw output (stdout/stderr) emitted by cargo deny check --format json.

    Returns:
        Parsed issues and a count of malformed JSON or finding records.
        Informational records and non-JSON log lines do not count as failures.
    """
    if not output or not output.strip():
        return [], 0

    issues: list[CargoDenyIssue] = []
    parse_failures_count = 0

    for line in output.splitlines():
        line = line.strip()
        # Ignore ordinary progress text; malformed object lines still count below.
        if not line or not line.startswith("{"):
            continue

        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                continue

            parsed, malformed = _parse_record(data)
            parse_failures_count += malformed
            if parsed is not None:
                issues.append(parsed)

        except json.JSONDecodeError as exc:
            logger.debug(f"Failed to decode cargo-deny JSON line: {exc}: {line}")
            parse_failures_count += 1

    return issues, parse_failures_count


def parse_cargo_deny_output(output: str) -> list[CargoDenyIssue]:
    """Compatibility wrapper returning cargo-deny issues without parse statistics.

    Args:
        output: Raw output emitted by cargo deny check --format json.

    Returns:
        Parsed issues, omitting informational and malformed records.
    """
    issues, _failures = parse_cargo_deny_output_with_stats(output)
    return issues
